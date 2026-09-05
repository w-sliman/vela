#!/bin/bash
# Pre-2015 test suites call httpbin.org over the internet. There is no internet
# here by design, so serve httpbin locally and point the suite at it via the
# HTTPBIN_URL hook those suites already honour. Newer suites use the
# pytest-httpbin fixture and ignore this entirely.
/work/env/bin/python - >/tmp/httpbin.log 2>&1 <<'PY' &
try:
    from httpbin import app
    app.run(host="127.0.0.1", port=8080, threaded=True)
except Exception as exc:
    print("httpbin unavailable:", exc)
PY
for _ in $(seq 40); do
    /work/env/bin/python -c "import socket;socket.create_connection(('127.0.0.1',8080),0.5)" 2>/dev/null && break
    sleep 0.5
done
export HTTPBIN_URL=http://127.0.0.1:8080/
