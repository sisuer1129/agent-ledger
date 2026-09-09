// A pager owns one submitted query. Invalidating it also invalidates in-flight work.
export function createTransactionPager(fetchPage, onChange = () => {}) {
  const state = { items: [], total: 0, nextOffset: 0, hasMore: true, loading: false, error: '', query: {} };
  let generation = 0;
  function apply(page) {
    const byId = new Map(state.items.map(item => [item.id, item]));
    page.items.forEach(item => byId.set(item.id, item));
    state.items = [...byId.values()];
    state.total = page.total;
    state.nextOffset = page.next_offset;
    state.hasMore = page.has_more;
  }
  async function loadMore() {
    if (state.loading || !state.hasMore) return;
    const current = generation;
    state.loading = true;
    state.error = '';
    onChange(state);
    try {
      const page = await fetchPage({ ...state.query, paginated: 1, limit: 50, offset: state.nextOffset });
      if (current !== generation) return;
      apply(page);
    } catch (error) {
      if (current !== generation) return;
      state.error = error.message;
    } finally {
      if (current === generation) { state.loading = false; onChange(state); }
    }
  }
  function invalidate() { generation += 1; state.loading = false; }
  async function reset(query, firstPage) {
    invalidate();
    Object.assign(state, { items: [], total: 0, nextOffset: 0, hasMore: true, loading: false, error: '', query: { ...query } });
    if (firstPage) { apply(firstPage); onChange(state); }
    else await loadMore();
  }
  return { state, reset, loadMore, invalidate };
}
