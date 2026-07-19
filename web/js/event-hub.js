function parsePayload(event) {
  try {
    return JSON.parse(event?.data || "{}");
  } catch {
    return {};
  }
}

export function createEventHub(open = () => new EventSource("/events")) {
  let source = null;
  const subscribers = new Map();
  const sourceListeners = new Map();

  function ensureSource() {
    if (!source) source = open();
    return source;
  }

  function subscribe(type, callback) {
    if (typeof callback !== "function") return () => {};
    const callbacks = subscribers.get(type) || new Set();
    subscribers.set(type, callbacks);
    callbacks.add(callback);

    if (!sourceListeners.has(type)) {
      const listener = (event) => {
        const payload = parsePayload(event);
        for (const subscriber of [...(subscribers.get(type) || [])]) {
          try {
            subscriber(payload, event);
          } catch (error) {
            console.error(`[EventHub:${type}] subscriber failed`, error);
          }
        }
      };
      sourceListeners.set(type, listener);
      ensureSource().addEventListener(type, listener);
    }

    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const remaining = subscribers.get(type);
      remaining?.delete(callback);
      if (remaining?.size === 0) {
        subscribers.delete(type);
        const listener = sourceListeners.get(type);
        if (listener) source?.removeEventListener?.(type, listener);
        sourceListeners.delete(type);
      }
      if (subscribers.size === 0 && source) {
        source.close();
        source = null;
      }
    };
  }

  function close() {
    for (const [type, listener] of sourceListeners) source?.removeEventListener?.(type, listener);
    sourceListeners.clear();
    subscribers.clear();
    source?.close();
    source = null;
  }

  return { subscribe, close };
}

export const eventHub = createEventHub();
