2026-01-09T09:04:08.124616052Z     return self.main(*args, **kwargs)
2026-01-09T09:04:08.124624843Z            ^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.124628103Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 1406, in main
2026-01-09T09:04:08.124888066Z     rv = self.invoke(ctx)
2026-01-09T09:04:08.124894556Z          ^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.124900946Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 1269, in invoke
2026-01-09T09:04:08.125184081Z     return ctx.invoke(self.callback, **ctx.params)
2026-01-09T09:04:08.12575352Z            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.12575997Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/click/core.py", line 824, in invoke
2026-01-09T09:04:08.12576395Z     return callback(*args, **kwargs)
2026-01-09T09:04:08.125766901Z            ^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.125769671Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/main.py", line 410, in main
2026-01-09T09:04:08.125772501Z     run(
2026-01-09T09:04:08.125775151Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/main.py", line 578, in run
2026-01-09T09:04:08.125777501Z     server.run()
2026-01-09T09:04:08.125780191Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/server.py", line 61, in run
2026-01-09T09:04:08.125782742Z     return asyncio.run(self.serve(sockets=sockets))
2026-01-09T09:04:08.125785452Z            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.125788162Z   File "/opt/render/project/python/Python-3.11.5/lib/python3.11/asyncio/runners.py", line 190, in run
2026-01-09T09:04:08.126010253Z     return runner.run(main)
2026-01-09T09:04:08.126021474Z            ^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.126029504Z   File "/opt/render/project/python/Python-3.11.5/lib/python3.11/asyncio/runners.py", line 118, in run
2026-01-09T09:04:08.12614025Z     return self._loop.run_until_complete(task)
2026-01-09T09:04:08.12615169Z            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.126155261Z   File "/opt/render/project/python/Python-3.11.5/lib/python3.11/asyncio/base_events.py", line 653, in run_until_complete
2026-01-09T09:04:08.12633951Z     return future.result()
2026-01-09T09:04:08.1263445Z            ^^^^^^^^^^^^^^^
2026-01-09T09:04:08.12634766Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/server.py", line 68, in serve
2026-01-09T09:04:08.126463136Z     config.load()
2026-01-09T09:04:08.126467596Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/config.py", line 473, in load
2026-01-09T09:04:08.126650816Z     self.loaded_app = import_from_string(self.app)
2026-01-09T09:04:08.126658656Z                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.126661756Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/importer.py", line 24, in import_from_string
2026-01-09T09:04:08.126786053Z     raise exc from None
2026-01-09T09:04:08.126791683Z   File "/opt/render/project/src/.venv/lib/python3.11/site-packages/uvicorn/importer.py", line 21, in import_from_string
2026-01-09T09:04:08.126881537Z     module = importlib.import_module(module_str)
2026-01-09T09:04:08.126891288Z              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.126895088Z   File "/opt/render/project/python/Python-3.11.5/lib/python3.11/importlib/__init__.py", line 126, in import_module
2026-01-09T09:04:08.127059787Z     return _bootstrap._gcd_import(name[level:], package, level)
2026-01-09T09:04:08.127099769Z            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.127104589Z   File "<frozen importlib._bootstrap>", line 1204, in _gcd_import
2026-01-09T09:04:08.127107389Z   File "<frozen importlib._bootstrap>", line 1176, in _find_and_load
2026-01-09T09:04:08.127110209Z   File "<frozen importlib._bootstrap>", line 1147, in _find_and_load_unlocked
2026-01-09T09:04:08.127112929Z   File "<frozen importlib._bootstrap>", line 690, in _load_unlocked
2026-01-09T09:04:08.127115899Z   File "<frozen importlib._bootstrap_external>", line 940, in exec_module
2026-01-09T09:04:08.12712594Z   File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed
2026-01-09T09:04:08.12712947Z   File "/opt/render/project/src/webhook_server.py", line 7, in <module>
2026-01-09T09:04:08.127221115Z     from main import application  # imports the built Application object
2026-01-09T09:04:08.127226895Z     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
2026-01-09T09:04:08.127229325Z ImportError: cannot import name 'application' from 'main' (/opt/render/project/src/main.py)