FROM public.ecr.aws/docker/library/python:3.12-slim

# Create non-root user (AgentBeats best practice)
RUN useradd -m -u 1000 agentbeats

WORKDIR /app

# Give agentbeats ownership of /app so it can create data/ subdirs at runtime
RUN chown agentbeats:agentbeats /app

COPY --chown=agentbeats:agentbeats requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=agentbeats:agentbeats src/ ./src/
COPY --chown=agentbeats:agentbeats main.py .

# Pre-create the data directory with correct ownership
RUN mkdir -p /app/data && chown agentbeats:agentbeats /app/data

USER agentbeats

EXPOSE 9009

ENTRYPOINT ["python", "main.py"]
CMD ["--host", "0.0.0.0", "--port", "9009"]
