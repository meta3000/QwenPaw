export const FIXED_SIDEBAR_ITEM_IDS: readonly string[] = [] as const;

export function isFixedSidebarItemId(itemId: string): boolean {
  return FIXED_SIDEBAR_ITEM_IDS.some((fixedId) => fixedId === itemId);
}
