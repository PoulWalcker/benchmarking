RUN python3 /tests/payload/environment/install.py
ENV PYTHONPATH=/tests/core:/tests:/tests/payload/vendor/autowfbench AUTOWFBENCH_ROOT=/tests/payload/vendor/autowfbench PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
