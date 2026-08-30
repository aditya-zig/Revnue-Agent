export function nextFocusIndex(currentIndex, focusableCount, backwards = false) {
  if (focusableCount <= 0) return -1;
  if (backwards) return currentIndex <= 0 ? focusableCount - 1 : currentIndex - 1;
  return currentIndex === focusableCount - 1 ? 0 : currentIndex + 1;
}
