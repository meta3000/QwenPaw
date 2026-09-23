/**
 * OsDrawer — thin wrapper around Antd Drawer.
 *
 * Previously lived in the Desktop OS shell (os/OsOverlay.tsx). After removing
 * the OS shell we keep a minimal re-export so existing consumers don't need
 * import-path changes.
 */
export { Drawer as OsDrawer } from "antd";
