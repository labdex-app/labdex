// One page of a long list for <Pager>. The page goes back to the first when `resetOn` changes
// (a new search or filter) and stays in range when the list shrinks.
import { computed, ref, unref, watch } from 'vue'

export const PAGE_SIZE = 50

export function usePaged(list, { size = PAGE_SIZE, resetOn = null } = {}) {
  const page = ref(0)
  const total = computed(() => (unref(list) || []).length)
  const rows = computed(() => (unref(list) || []).slice(page.value * size, (page.value + 1) * size))
  watch(total, (n) => { if (page.value * size >= n) page.value = Math.max(0, Math.ceil(n / size) - 1) })
  if (resetOn) watch(resetOn, () => { page.value = 0 }, { deep: true })
  return { page, rows, total, size }
}
