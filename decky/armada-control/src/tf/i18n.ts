// TF-card page dictionary. It is pruned to the keys this page actually uses:
// the TF strings plus the shared loading/refresh/review/cancel/details labels,
// the retry/error labels and the technical-details title. The Controller's own
// locale detection and dictionaries stay in ../i18n; this module only keeps the
// en/zh pair the migrated page was written for, and other Controller locales
// fall back to English at the call site.

export const en = {
  "common.loading": "Loading…",
  "common.refresh": "Refresh",
  "common.review": "Review",
  "common.cancel": "Cancel",
  "common.details": "Details",
  "details.title": "Technical details",
  "failure.retry": "Refresh and retry",
  "failure.errorLabel": "Error",
  "error.inspection_failed": "The disk layout could not be inspected.",
  "error.boot_disk_forbidden": "The boot disk cannot be used as the install target.",
  "error.boot_disk_unknown": "The boot disk could not be identified.",
  "error.target_changed": "The disk changed since review. Review again.",
  "error.status_unavailable": "The installation status is unavailable.",
  "error.confirmation_required": "The confirmation step is required before installing.",
  "error.unknown_token": "This plan has expired. Review again.",
  "error.unit_running": "Another conversion is already running.",
  "error.launch_failed": "The conversion could not be started.",
  "error.internal_error": "An internal error occurred.",
  "error.unknown": "Unexpected error.",
  "tf.title": "TF card game library",
  "tf.hint": "Insert a TF card prepared by the Android app (智能TF卡分配器). Only the Linux reserve partition is changed; Android data is kept.",
  "tf.card": "TF card",
  "tf.rescan": "Rescan",
  "tf.mount": "Mount and add to the library",
  "tf.none.title": "No prepared TF card found",
  "tf.none.body": "On Android, use the Smart TF Card Allocator (智能TF卡分配器) to allocate the card first, then insert it and rescan.",
  "tf.none.sdBoot": "If booting from the SD card is blocked, start Armada from the internal disk, then insert the card and rescan.",
  "tf.state.pending": "Allocated, waiting to convert",
  "tf.state.connected": "Linux card connected",
  "tf.state.recovery": "Unfinished conversion",
  "tf.state.blocked": "Blocked",
  "tf.stateLabel": "Status",
  "tf.androidLabel": "Android space kept",
  "tf.linuxLabel": "Linux space",
  "tf.sizeGiB": "{size} GiB",
  "tf.sizeUnknown": "unknown",
  "tf.confirm.format.title": "Format the Linux reserve partition?",
  "tf.confirm.format.description": "Only the Linux reserve partition is formatted to ext4. The Android metadata and private partitions (p2/p3) and their data are preserved. This cannot be undone.",
  "tf.confirm.format.erase": "Erases: the Linux reserve partition only. Android p2/p3 and their data are kept.",
  "tf.confirm.format.ok": "Format and continue",
  "tf.confirm.target": "Card: {device}.",
  "tf.confirm.reserve": "Linux reserve partition: {device}.",
  "tf.confirm.linux": "Linux space: {size} GiB.",
  "tf.confirm.android": "Android space kept: {size} GiB (p2/p3 preserved).",
  "tf.confirm.keepCard": "Keep the card inserted and do not power off while working.",
  "tf.confirm.mount.title": "Mount and add to the game library?",
  "tf.confirm.mount.description": "Mount this existing ext4 game library and connect it to Steam. Existing games are kept.",
  "tf.confirm.mount.ok": "Mount and add",
  "tf.running.title": "Preparing the TF card library",
  "tf.phase.preparing": "Preparing",
  "tf.phase.formatting": "Formatting the Linux reserve partition",
  "tf.phase.mounting": "Mounting",
  "tf.phase.registering": "Connecting to the game library",
  "tf.running.keepPowered": "Do not power off or remove the card until this finishes.",
  "tf.running.panelClose": "Closing this panel does not cancel the conversion.",
  "tf.running.pollUnavailable": "The conversion status cannot be read right now. Keep the card inserted.",
  "tf.success.title": "TF card library ready",
  "tf.success.registered": "The partition is mounted and connected to the Steam library.",
  "tf.success.pending": "The partition is mounted; waiting for Steam to pick up the game library.",
  "tf.success.retryConnect": "Retry connecting",
  "tf.success.backToCards": "Back to cards",
  "tf.failure.title": "TF card library conversion failed",
  "tf.failure.body": "The conversion did not finish. Keep the card inserted and retry. Android data on p2/p3 is preserved.",
  "error.unknown_device": "The selected card was not found. Rescan.",
  "error.handoff_missing": "The Android handoff was not found. On Android, use the Smart TF Card Allocator (智能TF卡分配器) to allocate the card first, then rescan.",
  "error.handoff_invalid": "The Android handoff is invalid. Reallocate the card with the Smart TF Card Allocator (智能TF卡分配器), then rescan.",
  "error.marker_missing": "The Linux handoff record is missing. Leave the partitions as they are, reinsert the card into the device that created it, then rescan.",
  "error.reserve_unprepared": "The reserve partition is neither a FAT/exFAT nor an ext4 volume. Reallocate the card from Android, then rescan.",
  "error.layout_unsupported": "The card layout is not a supported native three-partition GPT volume. Reallocate it from Android.",
  "error.identity_mismatch": "The card identity does not match the handoff. Rescan the card.",
  "error.filesystem_mismatch": "The filesystem does not match the handoff record. Rescan the card.",
  "error.filesystem_needs_recovery": "This ext4 card still needs journal recovery, so it was not mounted. Insert it into the device that wrote it, let it finish mounting, then rescan.",
  "error.card_busy": "The card is busy. Wait for the automount to finish, then retry.",
} as const;

export type TranslationKey = keyof typeof en;

export const zh: Record<TranslationKey, string> = {
  "common.loading": "正在加载…",
  "common.refresh": "刷新",
  "common.review": "检查",
  "common.cancel": "取消",
  "common.details": "详情",
  "details.title": "技术详情",
  "failure.retry": "刷新并重试",
  "failure.errorLabel": "错误",
  "error.inspection_failed": "无法检查磁盘布局。",
  "error.boot_disk_forbidden": "启动磁盘不能作为安装目标。",
  "error.boot_disk_unknown": "无法识别启动磁盘。",
  "error.target_changed": "自检查以来磁盘已变化，请重新检查。",
  "error.status_unavailable": "无法获取安装状态。",
  "error.confirmation_required": "安装前必须完成确认步骤。",
  "error.unknown_token": "该方案已失效，请重新检查。",
  "error.unit_running": "已有转换任务正在运行。",
  "error.launch_failed": "无法启动转换程序。",
  "error.internal_error": "发生内部错误。",
  "error.unknown": "发生未知错误。",
  "tf.title": "TF 卡游戏库",
  "tf.hint": "请插入由 Android 应用（智能TF卡分配器）分配好的 TF 卡。本操作仅处理 Linux 保留分区，Android 数据会保留。",
  "tf.card": "TF 卡",
  "tf.rescan": "重新扫描",
  "tf.mount": "挂载并加入游戏库",
  "tf.none.title": "未找到已分配的 TF 卡",
  "tf.none.body": "请先在 Android 上使用「智能TF卡分配器」完成 TF 卡分配，然后插入本机并重新扫描。",
  "tf.none.sdBoot": "如果从 SD 卡启动被阻止，请先从内部存储启动 Armada，再插入 TF 卡并重新扫描。",
  "tf.state.pending": "已分配，等待转换",
  "tf.state.connected": "已连接的 Linux 卡",
  "tf.state.recovery": "未完成的转换",
  "tf.state.blocked": "已阻止",
  "tf.stateLabel": "状态",
  "tf.androidLabel": "保留的 Android 空间",
  "tf.linuxLabel": "Linux 空间",
  "tf.sizeGiB": "{size} GiB",
  "tf.sizeUnknown": "未知",
  "tf.confirm.format.title": "格式化 Linux 保留分区？",
  "tf.confirm.format.description": "仅将 Linux 保留分区格式化为 ext4。Android 的元数据与私有分区（p2/p3）及其数据都会保留，此操作无法撤销。",
  "tf.confirm.format.erase": "仅擦除：Linux 保留分区。Android 的 p2/p3 及其数据都会保留。",
  "tf.confirm.format.ok": "格式化并继续",
  "tf.confirm.target": "TF 卡：{device}。",
  "tf.confirm.reserve": "Linux 保留分区：{device}。",
  "tf.confirm.linux": "Linux 空间：{size} GiB。",
  "tf.confirm.android": "保留的 Android 空间：{size} GiB（p2/p3 保留）。",
  "tf.confirm.keepCard": "处理过程中请保持 TF 卡插入，切勿关机。",
  "tf.confirm.mount.title": "挂载并加入游戏库？",
  "tf.confirm.mount.description": "挂载现有 ext4 游戏库并接入 Steam，保留已有游戏。",
  "tf.confirm.mount.ok": "挂载并加入",
  "tf.running.title": "正在处理 TF 卡游戏库",
  "tf.phase.preparing": "正在准备",
  "tf.phase.formatting": "正在格式化 Linux 保留分区",
  "tf.phase.mounting": "正在挂载",
  "tf.phase.registering": "正在接入游戏库",
  "tf.running.keepPowered": "完成前请勿关机或拔出 TF 卡。",
  "tf.running.panelClose": "关闭此面板不会取消转换。",
  "tf.running.pollUnavailable": "暂时无法读取转换状态，请保持 TF 卡插入。",
  "tf.success.title": "TF 卡游戏库已就绪",
  "tf.success.registered": "分区已挂载，已接入 Steam 游戏库。",
  "tf.success.pending": "分区已挂载，等待 Steam 接入游戏库。",
  "tf.success.retryConnect": "重试接入游戏库",
  "tf.success.backToCards": "返回卡列表",
  "tf.failure.title": "TF 卡游戏库处理失败",
  "tf.failure.body": "转换未完成。请保持 TF 卡插入后重试，Android 的 p2/p3 数据会保留。",
  "error.unknown_device": "未找到所选 TF 卡，请重新扫描。",
  "error.handoff_missing": "未找到 Android 交接记录。请先在 Android 上使用「智能TF卡分配器」完成分配，再重新扫描。",
  "error.handoff_invalid": "Android 交接记录无效。请使用「智能TF卡分配器」重新分配后再扫描。",
  "error.marker_missing": "缺少 Linux 交接记录。请保持分区不变，把该卡插回创建它的设备检查后重新扫描。",
  "error.reserve_unprepared": "保留分区既不是 FAT/exFAT 也不是 ext4。请从 Android 重新分配后再扫描。",
  "error.layout_unsupported": "该卡不是受支持的原生三分区 GPT 布局。请从 Android 重新分配。",
  "error.identity_mismatch": "TF 卡身份与交接记录不匹配，请重新扫描。",
  "error.filesystem_mismatch": "文件系统与交接记录不匹配，请重新扫描。",
  "error.filesystem_needs_recovery": "该 ext4 卡仍需日志恢复，本次未挂载。请先在写入该卡的设备上完成挂载，然后重新扫描。",
  "error.card_busy": "TF 卡正忙，请等待自动挂载结束后重试。",
};

export type Locale = "en" | "zh";

export const localeStrings: Record<Locale, Record<TranslationKey, string>> = { en, zh };

type Variables = Record<string, string | number>;

function interpolate(text: string, variables?: Variables): string {
  if (!variables) return text;
  return Object.entries(variables).reduce(
    (result, [key, value]) => result.split(`{${key}}`).join(String(value)),
    text,
  );
}

export function translate(locale: Locale, key: TranslationKey, variables?: Variables): string {
  return interpolate(localeStrings[locale][key], variables);
}

export function translateErrorCode(locale: Locale, code: string): string {
  if (!code) return "";
  const key = `error.${code}`;
  return Object.prototype.hasOwnProperty.call(localeStrings.en, key)
    ? localeStrings[locale][key as TranslationKey]
    : translate(locale, "error.internal_error");
}
