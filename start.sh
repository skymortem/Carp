echo '#!/bin/bash
cd ~/carp/backend && PYTHONPATH="" .venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000' > ~/carp/start.sh && chmod +x ~/carp/start.sh
