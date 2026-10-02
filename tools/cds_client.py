# -*- coding: utf-8 -*-
"""CDS-Beta(新 Copernicus Climate Data Store)极简客户端。

只依赖 requests;凭据读 ~/.cdsapirc(url + key)。新 API 流程:
    POST {url}/retrieve/v1/processes/{dataset}/execute   -> {"processID": ...}
    GET  {url}/retrieve/v1/jobs/{processID}               -> status
    GET  {url}/retrieve/v1/jobs/{processID}/results       -> 下载 zip/nc

注意:WSL 下 ~/.cdsapirc 不存在时,回落到 Windows 的 C:/Users/<u>/.cdsapirc。
"""
from __future__ import annotations

import json
import os
import time

import requests

_CFG_PATHS = [
    os.path.expanduser('~/.cdsapirc'),
    '/mnt/c/Users/Yukioto/.cdsapirc',
]


def load_credentials():
    for p in _CFG_PATHS:
        if os.path.exists(p):
            cfg = {}
            with open(p, encoding='utf-8') as f:
                for line in f:
                    if ':' in line:
                        k, v = line.split(':', 1)
                        cfg[k.strip()] = v.strip()
            if cfg.get('url') and cfg.get('key'):
                return cfg['url'].rstrip('/'), cfg['key']
    raise RuntimeError('找不到 CDS 凭证(.cdsapirc)')


class CDS:
    def __init__(self, url=None, key=None, timeout=60):
        if url is None:
            url, key = load_credentials()
        self.url = url.rstrip('/')
        self.key = key
        self.timeout = timeout

    @property
    def _hdr(self):
        return {'PRIVATE-TOKEN': self.key, 'Content-Type': 'application/json'}

    def submit(self, dataset: str, request: dict) -> str:
        r = requests.post(f'{self.url}/retrieve/v1/processes/{dataset}/execute',
                          headers=self._hdr, json={'inputs': request},
                          timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        # 新 API 返回 {"processID": <数据集名>, "jobID": <uuid>, "status": ...}
        # —— 轮询/下载要用 jobID,不能用 processID(那是数据集名,拿它去查会 404)
        return j.get('jobID') or j.get('jobId') or j.get('id')

    def status(self, pid: str) -> dict:
        r = requests.get(f'{self.url}/retrieve/v1/jobs/{pid}',
                         headers=self._hdr, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def wait(self, pid: str, poll=10, max_wait=7200, verbose=True):
        t0 = time.time()
        while True:
            st = self.status(pid)
            s = (st.get('status') or '').lower()
            if verbose:
                print('  [cds] %s  %s' % (s, st.get('progress', '')), flush=True)
            if s in ('completed', 'successful', 'finished'):
                return st
            if s in ('failed', 'dismissed', 'rejected'):
                raise RuntimeError('CDS 任务失败: %s' % json.dumps(st)[:400])
            if time.time() - t0 > max_wait:
                raise TimeoutError('CDS 任务超时 pid=%s' % pid)
            time.sleep(poll)

    def download(self, pid: str, out_path: str, retries: int = 4) -> str:
        """下载产物。CDS 的 results 端点会断流(实测 46MB 只下到 2.2MB 就
        IncompleteRead), 必须重试 —— 而任务在服务端已经算完, 重试只是重下,
        不会重新排队。"""
        last = None
        for k in range(retries):
            try:
                return self._download_once(pid, out_path)
            except Exception as e:
                last = e
                print('  [cds] 下载失败(%d/%d): %s' % (k + 1, retries, str(e)[:80]),
                      flush=True)
                time.sleep(5 + 10 * k)
        raise last

    def _download_once(self, pid: str, out_path: str) -> str:
        # requests 的流式下载对 CDS 的对象存储稳定在 2-4 MB 处断流
        # (IncompleteRead, 48 MB 的文件下不到 10%), 换 curl:
        #   --retry 8 --retry-all-errors  网络抖动自动重试
        #   -C -                          断点续传(重试从已下字节接着下)
        #   大 --speed-time/--speed-limit  卡死时主动断开重试, 而不是干等
        import subprocess
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        url = f'{self.url}/retrieve/v1/jobs/{pid}/results'
        tmp = out_path + '.part'
        # 关键: 上一轮失败留下的 .part 里可能是 **JSON 跳转体**(330 B)。
        # 若直接 -C - 续传, 会拼出"JSON 头 + NetCDF 体"的畸形文件
        # (实测 840 KB, NetCDF 报 Unknown file format)。小文件一律先删。
        if os.path.exists(tmp) and os.path.getsize(tmp) < 100_000:
            os.remove(tmp)
        r = subprocess.run(
            ['curl', '-sSL', '--noproxy', '*', '-m', '1800',
             '--retry', '8', '--retry-all-errors', '--retry-delay', '5',
             '--speed-time', '60', '--speed-limit', '2048',
             '-C', '-', '-H', 'PRIVATE-TOKEN: ' + self.key,
             '-o', tmp, '-w', '%{http_code}', url],
            capture_output=True)
        code = (r.stdout or b'').decode().strip()
        # 阈值必须低于 JSON 跳转体(330 B), 否则跳转体被当成"下载失败"拦下
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 200:
            raise RuntimeError('curl 下载失败 http=%s %s'
                               % (code, (r.stderr or b'')[:200]))
        # /results 有两种返回: 直接给文件字节, 或给一段 JSON
        # {"asset":{"value":{"href":"https://object-store..."}}} 指向真实文件。
        # 不判断就会把 330 字节的 JSON 当成 .nc 存下来(而且 http=200 看似成功)。
        with open(tmp, 'rb') as f:
            _head = f.read(16).lstrip()
        if _head.startswith(b'{'):
            try:
                with open(tmp, 'rb') as f:
                    j = json.loads(f.read().decode('utf-8', 'replace'))
                href = (((j.get('asset') or {}).get('value') or {}).get('href'))
            except Exception:
                href = None
            if href:
                os.remove(tmp)          # 别把 JSON 留在那里给下一次续传
                r2 = subprocess.run(
                    ['curl', '-sSL', '--noproxy', '*', '-m', '1800',
                     '--retry', '8', '--retry-all-errors', '--retry-delay', '5',
                     '--speed-time', '60', '--speed-limit', '2048',
                     '-C', '-', '-o', tmp, '-w', '%{http_code}', href],
                    capture_output=True)
                if os.path.getsize(tmp) < 1024:
                    raise RuntimeError('对象存储下载失败 http=%s'
                                       % (r2.stdout or b'').decode().strip())
        os.replace(tmp, out_path)
        return out_path

    def _download_once_requests(self, pid: str, out_path: str) -> str:
        r = requests.get(f'{self.url}/retrieve/v1/jobs/{pid}/results',
                         headers=self._hdr, timeout=self.timeout, stream=True)
        r.raise_for_status()
        ctype = (r.headers.get('Content-Type') or '').lower()
        first = r.raw.read(1 << 16, decode_content=True)
        # 新 CDS 的 /results 有两种返回: 直接给文件字节, 或给一段 JSON
        # {"asset":{"value":{"href": "https://object-store..."}}} 指向真实文件。
        # 不判断的话会把 330 字节的 JSON 当成 .nc 存下来(而且"成功"了)。
        href = None
        if b'json' in ctype.encode() or first.lstrip()[:1] == b'{':
            try:
                j = json.loads(first.decode('utf-8', 'replace'))
                href = (((j.get('asset') or {}).get('value') or {}).get('href'))
            except Exception:
                href = None
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        tmp = out_path + '.part'
        if href:
            with requests.get(href, stream=True, timeout=self.timeout) as rr:
                rr.raise_for_status()
                with open(tmp, 'wb') as f:
                    for chunk in rr.iter_content(1 << 20):
                        f.write(chunk)
        else:
            with open(tmp, 'wb') as f:
                f.write(first)
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(tmp, out_path)
        return out_path

    def retrieve(self, dataset: str, request: dict, out_path: str,
                 poll=10, verbose=True) -> str:
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        pid = self.submit(dataset, request)
        if verbose:
            print('  [cds] 已提交 %s -> %s' % (dataset, pid), flush=True)
        self.wait(pid, poll=poll, verbose=verbose)
        self.download(pid, out_path)
        return out_path


if __name__ == '__main__':
    c = CDS()
    print('url =', c.url, ' key_len =', len(c.key))
    ds = 'reanalysis-era5-single-levels-monthly-means'
    req = {
        'product_type': ['monthly_averaged_reanalysis'],
        'variable': ['sea_surface_temperature'],
        'year': ['2020'], 'month': ['09'], 'time': ['00:00'],
        'data_format': 'netcdf', 'download_format': 'unarchived',
        'area': [60, 0, -60, 360],
    }
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'output', '_cds_test.nc')
    c.retrieve(ds, req, out, verbose=True)
    print('OK ->', out, os.path.getsize(out), 'bytes')
