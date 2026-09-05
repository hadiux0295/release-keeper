# AgentCore Runtime container — ARM64 is mandatory (docs: HTTP protocol contract).
# Build on x86 with:  docker buildx build --platform linux/arm64 -t release-keeper:arm64 --load .
FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY release_keeper ./release_keeper
RUN pip install --no-cache-dir ".[web]"

# Model endpoint: pass RK_MODEL / RK_API_BASE / RK_API_KEY as runtime environment variables.
EXPOSE 8080
CMD ["uvicorn", "release_keeper.agentcore:app", "--host", "0.0.0.0", "--port", "8080"]
