/** Config form helpers (non-i18n). */

export function humanizeKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Normalize API section name to i18n key segment (e.g. "GIT CONFIGURATION" → "GIT_CONFIGURATION"). */
export function sectionSlug(section: string): string {
  return section.replace(/\s+/g, "_");
}

const PATH_LIKE_KEYS = new Set([
  "GIT_REPO_URL",
  "TORRENT_SAVE_PATH",
  "REPORTS_DIR",
  "DAILY_REPORT_DIR",
  "AD_HOC_DIR",
  "PARSED_MOVIES_CSV",
  "HISTORY_DB_PATH",
  "REPORTS_DB_PATH",
  "OPERATIONS_DB_PATH",
  "SPIDER_LOG_FILE",
  "UPLOADER_LOG_FILE",
  "PIPELINE_LOG_FILE",
  "EMAIL_NOTIFICATION_LOG_FILE",
  "PIKPAK_LOG_FILE",
  "QB_FILE_FILTER_LOG_FILE",
  "RCLONE_ROOT_FOLDER",
  "RCLONE_INVENTORY_CSV",
  "DEDUP_CSV",
  "DEDUP_DIR",
  "DEDUP_LOG_FILE",
]);

export function isPathLikeField(key: string, type: string, sensitive: boolean): boolean {
  return type === "string" && !sensitive && PATH_LIKE_KEYS.has(key);
}

/** Select field option values (labels come from i18n `config.select.<KEY>.<value>`). */
export const SELECT_OPTION_KEYS: Record<string, readonly string[]> = {
  QB_SCHEME: ["https", "http"],
  PROXY_MODE: ["pool", "single"],
  STORAGE_MODE: ["duo", "db", "csv"],
  LOG_LEVEL: ["DEBUG", "INFO", "WARNING", "ERROR"],
};
