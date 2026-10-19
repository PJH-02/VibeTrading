"""Test only namespaced testnet keys in known app configuration files."""
import asyncio
import hashlib
import json
from pathlib import Path

import aiohttp
from dotenv import dotenv_values

async def main():
    results=[]; seen={}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
        for label,path in [('runtime','/audit/runtime.env'),('project','/audit/project.env')]:
            if not Path(path).exists(): continue
            env=dotenv_values(path); key=env.get('GRVT_TESTNET_API_KEY')
            row={'configuration':label,'testnet_key_configured':bool(key)}
            if key:
                digest=hashlib.sha256(key.encode()).hexdigest()
                if digest in seen:
                    row.update(same_key_as=seen[digest]); results.append(row); continue
                seen[digest]=label
                async with session.post('https://edge.testnet.grvt.io/auth/api_key/login',json={'api_key':key}) as response:
                    row['http_status']=response.status
                    row['authenticated']=response.status==200 and bool(response.cookies.get('gravity'))
            results.append(row)
    print(json.dumps(results))

asyncio.run(main())
