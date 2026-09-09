/**
 * 简单防抖：适用于搜索框 @input 触发远程查询
 * 返回的函数以最后一次调用为准，delay 毫秒后执行 fn
 */
export function debounce<A extends unknown[]>(fn: (...args: A) => void, delay = 300) {
  let timer: ReturnType<typeof setTimeout> | undefined
  return (...args: A) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => fn(...args), delay)
  }
}
