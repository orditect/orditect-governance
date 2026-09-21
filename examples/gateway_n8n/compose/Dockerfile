# Demo image: framework (orditect, sibling checkout) + governance
# stack + gateway. The build context is the PARENT directory of the
# two sibling checkouts, so both source trees are visible.
FROM python:3.12-slim

WORKDIR /app

COPY orditect/ /app/orditect/
COPY orditect-governance/ /app/orditect-governance/

RUN pip install --no-cache-dir \
    -e /app/orditect/packages/protocol \
    -e /app/orditect/packages/core \
    -e /app/orditect/packages/flow \
    -e /app/orditect/packages/stream \
    -e /app/orditect/packages/adapter-memory \
    -e /app/orditect/packages/adapter-local \
    -e /app/orditect/packages/adapter-ui \
    -e /app/orditect/packages/bridge-openai \
    -e /app/orditect-governance/packages/ordigovernance-api \
    -e /app/orditect-governance/packages/ordigovernance-runtime \
    -e /app/orditect-governance/packages/ordigovernance-testing \
    -e /app/orditect-governance/packages/ordigovernance-viewer \
    -e /app/orditect-governance/packages/ordigovernance-bridges-direct \
    -e /app/orditect-governance/packages/ordigovernance-gateway[memory]

# examples/ is not an installed distribution; the demo registry and
# the viewer host import it from the source tree.
ENV PYTHONPATH=/app/orditect-governance