// Minimal observable state container. No DOM access, so it stays testable and
// free of import-time side effects.
export function createStore(initial = {}) {
  const state = { ...initial };
  const subscribers = new Set();
  return {
    state,
    get: key => state[key],
    set(patch) {
      Object.assign(state, patch);
      subscribers.forEach(fn => fn(state));
      return state;
    },
    subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },
  };
}
