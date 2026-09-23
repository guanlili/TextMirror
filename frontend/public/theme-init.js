try {
  if (globalThis.localStorage.getItem('tm_theme') === 'dark') {
    globalThis.document.documentElement.classList.add('dark')
  }
} catch {
  // Storage may be unavailable under browser privacy restrictions.
}
