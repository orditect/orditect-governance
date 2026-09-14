/* SSE (EventSource) wrapper with auto-reconnect and JSON parsing.

The source knows nothing about event semantics; consumers register
onMessage handlers and receive parsed payloads.
*/

export function initSse({ url, eventSourceImpl = null } = {}) {
  const ES = eventSourceImpl || EventSource;
  const handlers = { message: [], error: [], open: [] };
  let source = null;

  const connect = () => {
    source = new ES(url);
    source.onopen = () => handlers.open.forEach((fn) => fn());
    source.onmessage = (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch {
        data = e.data;
      }
      handlers.message.forEach((fn) => fn(data, e));
    };
    source.onerror = (err) => handlers.error.forEach((fn) => fn(err));
  };

  connect();

  return {
    onMessage: (fn) => handlers.message.push(fn),
    onError: (fn) => handlers.error.push(fn),
    onOpen: (fn) => handlers.open.push(fn),
    close: () => source && source.close(),
    readyState: () => (source ? source.readyState : EventSource.CLOSED),
  };
}