//! Dedup-related hot path operations.
//!
//! UNCENSORED_SENSOR_PRIORITY mirrors `utils.contracts.UNCENSORED_SENSOR_PRIORITY`.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// Size ratio threshold mirroring Python's ``SIZE_THRESHOLD_RATIO`` (1.30).
const SIZE_THRESHOLD_RATIO: f64 = 1.30;

const SENSOR_YOUMA: &str = "有码";
const SUBTITLE_ZHONGZI: &str = "中字";
const SUBTITLE_WUZI: &str = "无字";

fn as_upper(s: &str) -> String {
    s.trim().to_uppercase()
}

/// Priority table mirroring Python's UNCENSORED_SENSOR_PRIORITY.
fn uncensored_sensor_priority(sensor: &str) -> u8 {
    match sensor {
        "无码流出" => 3,
        "无码" => 2,
        "无码破解" => 1,
        _ => 0,
    }
}

fn is_wuma_category(sensor: &str) -> bool {
    uncensored_sensor_priority(sensor) > 0
}

// ============================================================================
// Folder-dedup ranking cascade (ADR-048 Phase 3a)
//
// Rust owns the KEEP/DELETE *decision* only. The purge execution and the
// human-readable reason strings stay in Python — here we return enough context
// (which rule fired + the referenced sensors) for Python to rebuild the EXACT
// existing reason strings byte-for-byte.
//
// Folders cross the boundary by their *input index* (stable identity, robust
// against duplicate full_paths). The decision is expressed purely in terms of
// those indices.
// ============================================================================

/// A single input folder, reduced to the fields the decision depends on.
/// `index` is the folder's position in the original input list — the only
/// identity Python needs to reattach the original `FolderInfo` object.
#[derive(Clone)]
struct DedupFolder {
    index: usize,
    sensor: String,
    subtitle: String,
    size: i64,
}

/// Which rule justified a deletion. Carries the referenced sensors so Python
/// can rebuild the exact reason string templated in `dedup.py`.
#[derive(Debug, PartialEq, Eq)]
enum DeleteRule {
    /// Rule1 — uncensored sensor priority loser.
    /// `keep_sensor` > `loser_sensor`.
    SensorPriority {
        keep_sensor: String,
        loser_sensor: String,
    },
    /// Rule2 — a subtitle version exists in the YOUMA group, so this no-subtitle
    /// folder is dropped. `category_name` is always "有码".
    SubtitleYouma { category_name: String },
    /// Rule2 — a subtitle version exists in the WUMA group, so this no-subtitle
    /// folder is dropped. `kept_zhongzi_sensor` is the sensor of the kept 中字.
    SubtitleWuma { kept_zhongzi_sensor: String },
}

/// One delete decision: the input index plus the rule that fired.
#[derive(Debug, PartialEq, Eq)]
struct DeleteDecision {
    index: usize,
    rule: DeleteRule,
}

/// The cascade's verdict for one movie_code: which input indices to keep and
/// which to delete (with rule context).
#[derive(Debug, PartialEq, Eq)]
struct DedupDecision {
    keep: Vec<usize>,
    delete: Vec<DeleteDecision>,
}

/// Errors raised by the cascade core. These map to Python exceptions and make
/// the D5 invariants fail-closed instead of silently losing folders.
#[derive(Debug, PartialEq, Eq)]
enum DedupError {
    /// A folder could not be classified (sensor not 有码/wuma, or subtitle not
    /// 中字/无字). Python silently drops these — Rust fails closed.
    UnclassifiableFolder { index: usize, detail: String },
    /// A non-empty input produced an empty keep set — the cascade must never
    /// purge every copy.
    EmptyKeep,
    /// keep ∪ delete != input, or keep ∩ delete != ∅.
    PartitionViolation { detail: String },
    /// More than one sensor-priority winner kept within a wuma subtitle group.
    MultipleSensorWinners { detail: String },
}

impl std::fmt::Display for DedupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DedupError::UnclassifiableFolder { index, detail } => write!(
                f,
                "dedup invariant: folder at index {index} cannot be classified ({detail})"
            ),
            DedupError::EmptyKeep => {
                write!(f, "dedup invariant: non-empty input yielded empty keep set")
            }
            DedupError::PartitionViolation { detail } => {
                write!(f, "dedup invariant: keep/delete partition violated ({detail})")
            }
            DedupError::MultipleSensorWinners { detail } => write!(
                f,
                "dedup invariant: more than one sensor-priority winner ({detail})"
            ),
        }
    }
}

/// D5 invariant check: `keep ∪ delete == 0..n` and `keep ∩ delete == ∅`.
fn check_partition(
    keeps: &[usize],
    deletes: &[DeleteDecision],
    n: usize,
) -> Result<(), DedupError> {
    let mut seen: Vec<usize> = keeps.to_vec();
    for d in deletes {
        seen.push(d.index);
    }
    seen.sort_unstable();
    let total = seen.len();
    seen.dedup();
    if seen.len() != total {
        return Err(DedupError::PartitionViolation {
            detail: "a folder appears in more than one of keep/delete".to_string(),
        });
    }
    // `seen` is already sorted+deduped: require it to equal 0..n exactly, so a
    // gap (missing index) or an out-of-range index is rejected even when the
    // count happens to match n.
    let expected: Vec<usize> = (0..n).collect();
    if seen != expected {
        return Err(DedupError::PartitionViolation {
            detail: format!("covered indices {seen:?}, expected 0..{n}"),
        });
    }
    Ok(())
}

/// D5 invariant check: a non-empty input never yields an empty keep set.
fn check_nonempty_keep(keeps: &[usize], n: usize) -> Result<(), DedupError> {
    if n > 0 && keeps.is_empty() {
        return Err(DedupError::EmptyKeep);
    }
    Ok(())
}

/// D5 invariant check: at most one sensor-priority winner per wuma subtitle group.
fn check_single_sensor_winner(
    kept_zhongzi_len: usize,
    kept_wuzi_len: usize,
) -> Result<(), DedupError> {
    if kept_zhongzi_len > 1 || kept_wuzi_len > 1 {
        return Err(DedupError::MultipleSensorWinners {
            detail: format!("kept_zhongzi={kept_zhongzi_len}, kept_wuzi={kept_wuzi_len}"),
        });
    }
    Ok(())
}

/// Stable, priority-descending sort key: returns the index (into `folders`) of
/// the FIRST folder having the maximum sensor priority. Mirrors Python's STABLE
/// `sorted(..., reverse=True)[0]` — equal-priority folders keep input order, so
/// the winner is the first one in input order among the top priority.
fn first_max_priority(folders: &[DedupFolder]) -> usize {
    let mut best = 0usize;
    let mut best_prio = uncensored_sensor_priority(&folders[0].sensor);
    for (i, f) in folders.iter().enumerate().skip(1) {
        let prio = uncensored_sensor_priority(&f.sensor);
        if prio > best_prio {
            best_prio = prio;
            best = i;
        }
    }
    best
}

/// Apply sensor-priority within a (single-subtitle) wuma group.
/// Returns the kept folder (at most one) and pushes losers to `deletes`.
/// Mirrors Python `_apply_sensor_priority`.
fn apply_sensor_priority(
    folders: &[DedupFolder],
    deletes: &mut Vec<DeleteDecision>,
) -> Vec<DedupFolder> {
    if folders.is_empty() {
        return Vec::new();
    }
    if folders.len() == 1 {
        return folders.to_vec();
    }
    let winner_pos = first_max_priority(folders);
    let keep = folders[winner_pos].clone();
    for (i, loser) in folders.iter().enumerate() {
        if i == winner_pos {
            continue;
        }
        deletes.push(DeleteDecision {
            index: loser.index,
            rule: DeleteRule::SensorPriority {
                keep_sensor: keep.sensor.clone(),
                loser_sensor: loser.sensor.clone(),
            },
        });
    }
    vec![keep]
}

/// Process the YOUMA group (all sensor == 有码) by subtitle.
/// Mirrors Python `_process_subtitle_dedup(result, folders, "有码")`.
fn process_subtitle_dedup(
    folders: &[DedupFolder],
    keeps: &mut Vec<usize>,
    deletes: &mut Vec<DeleteDecision>,
) {
    if folders.is_empty() {
        return;
    }
    let zhongzi: Vec<&DedupFolder> = folders
        .iter()
        .filter(|f| f.subtitle == SUBTITLE_ZHONGZI)
        .collect();
    let wuzi: Vec<&DedupFolder> = folders
        .iter()
        .filter(|f| f.subtitle == SUBTITLE_WUZI)
        .collect();

    if !zhongzi.is_empty() && !wuzi.is_empty() {
        for z in &zhongzi {
            keeps.push(z.index);
        }
        let zhongzi_size = zhongzi.iter().map(|f| f.size).max().unwrap_or(0);
        for wf in &wuzi {
            if zhongzi_size > 0 && (wf.size as f64) > (zhongzi_size as f64) * SIZE_THRESHOLD_RATIO {
                keeps.push(wf.index); // size exception
            } else {
                deletes.push(DeleteDecision {
                    index: wf.index,
                    rule: DeleteRule::SubtitleYouma {
                        category_name: SENSOR_YOUMA.to_string(),
                    },
                });
            }
        }
    } else {
        for f in &zhongzi {
            keeps.push(f.index);
        }
        for f in &wuzi {
            keeps.push(f.index);
        }
    }
}

/// Process the WUMA group by subtitle + sensor priority.
/// Mirrors Python `_process_wuma_dedup`.
fn process_wuma_dedup(
    folders: &[DedupFolder],
    keeps: &mut Vec<usize>,
    deletes: &mut Vec<DeleteDecision>,
) -> Result<(), DedupError> {
    if folders.is_empty() {
        return Ok(());
    }
    let zhongzi: Vec<DedupFolder> = folders
        .iter()
        .filter(|f| f.subtitle == SUBTITLE_ZHONGZI)
        .cloned()
        .collect();
    let wuzi: Vec<DedupFolder> = folders
        .iter()
        .filter(|f| f.subtitle == SUBTITLE_WUZI)
        .cloned()
        .collect();

    let kept_zhongzi = apply_sensor_priority(&zhongzi, deletes);
    let kept_wuzi = apply_sensor_priority(&wuzi, deletes);

    // D5 invariant: at most one sensor-priority winner per subtitle group.
    check_single_sensor_winner(kept_zhongzi.len(), kept_wuzi.len())?;

    if !kept_zhongzi.is_empty() {
        for z in &kept_zhongzi {
            keeps.push(z.index);
        }
        let zhongzi_size = kept_zhongzi.iter().map(|f| f.size).max().unwrap_or(0);
        let kept_zhongzi_sensor = kept_zhongzi[0].sensor.clone();
        for wf in &kept_wuzi {
            if zhongzi_size > 0 && (wf.size as f64) > (zhongzi_size as f64) * SIZE_THRESHOLD_RATIO {
                keeps.push(wf.index); // size exception
            } else {
                deletes.push(DeleteDecision {
                    index: wf.index,
                    rule: DeleteRule::SubtitleWuma {
                        kept_zhongzi_sensor: kept_zhongzi_sensor.clone(),
                    },
                });
            }
        }
    } else {
        for w in &kept_wuzi {
            keeps.push(w.index);
        }
    }
    Ok(())
}

/// Pure-Rust core of the folder-dedup cascade. No `Python<'_>`/PyDict in the
/// signature so it stays linkable under `cargo test`.
///
/// Mirrors `analyze_duplicates_for_code`, but enforces the ADR-048 D5
/// invariants fail-closed (returns `Err` instead of silently dropping folders).
fn analyze_duplicates_core(folders: &[DedupFolder]) -> Result<DedupDecision, DedupError> {
    // Single/empty → keep all, delete none (matches the early return upstream).
    if folders.len() <= 1 {
        return Ok(DedupDecision {
            keep: folders.iter().map(|f| f.index).collect(),
            delete: Vec::new(),
        });
    }

    // D5 partition (fail-closed): every folder must classify. Python silently
    // drops the unclassifiable ones — a latent bug. We refuse instead.
    for f in folders {
        let sensor_ok = f.sensor == SENSOR_YOUMA || is_wuma_category(&f.sensor);
        let subtitle_ok = f.subtitle == SUBTITLE_ZHONGZI || f.subtitle == SUBTITLE_WUZI;
        if !sensor_ok || !subtitle_ok {
            return Err(DedupError::UnclassifiableFolder {
                index: f.index,
                detail: format!("sensor={:?}, subtitle={:?}", f.sensor, f.subtitle),
            });
        }
    }

    let youma: Vec<DedupFolder> = folders
        .iter()
        .filter(|f| f.sensor == SENSOR_YOUMA)
        .cloned()
        .collect();
    let wuma: Vec<DedupFolder> = folders
        .iter()
        .filter(|f| is_wuma_category(&f.sensor))
        .cloned()
        .collect();

    let mut keeps: Vec<usize> = Vec::new();
    let mut deletes: Vec<DeleteDecision> = Vec::new();

    process_subtitle_dedup(&youma, &mut keeps, &mut deletes);
    process_wuma_dedup(&wuma, &mut keeps, &mut deletes)?;

    // D5 invariants (fail-closed): total+disjoint partition, never-empty keep.
    check_partition(&keeps, &deletes, folders.len())?;
    check_nonempty_keep(&keeps, folders.len())?;

    Ok(DedupDecision {
        keep: keeps,
        delete: deletes,
    })
}

#[pyfunction]
pub fn should_skip_from_rclone<'py>(
    video_code: &str,
    entries: Vec<Bound<'py, PyDict>>,
    enable_dedup: bool,
) -> PyResult<bool> {
    if enable_dedup {
        return Ok(false);
    }
    let code = as_upper(video_code);
    if code.is_empty() {
        return Ok(false);
    }
    for entry in &entries {
        let vc: String = entry
            .get_item("video_code")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        if as_upper(&vc) != code {
            continue;
        }
        let subtitle: String = entry
            .get_item("subtitle_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        if subtitle == "中字" {
            return Ok(true);
        }
    }
    Ok(false)
}

#[pyfunction]
pub fn check_dedup_upgrade<'py>(
    py: Python<'py>,
    video_code: &str,
    new_torrent_types: Bound<'py, PyDict>,
    entries: Vec<Bound<'py, PyDict>>,
) -> PyResult<Vec<Py<PyDict>>> {
    let has_subtitle = new_torrent_types
        .get_item("subtitle")?
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false)
        || new_torrent_types
            .get_item("hacked_subtitle")?
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

    let hacked = new_torrent_types
        .get_item("hacked_subtitle")?
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false)
        || new_torrent_types
            .get_item("hacked_no_subtitle")?
            .and_then(|v| v.extract::<bool>().ok())
            .unwrap_or(false);

    // Infer new sensor priority: non-hacked implies 無碼 (prio 2)
    let inferred_new_prio: u8 = if !hacked {
        uncensored_sensor_priority("无码")
    } else {
        0
    };

    let now_str = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let mut out = Vec::new();

    for entry in &entries {
        let sensor: String = entry
            .get_item("sensor_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let subtitle: String = entry
            .get_item("subtitle_category")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let folder_path: String = entry
            .get_item("folder_path")?
            .and_then(|v| v.extract().ok())
            .unwrap_or_default();
        let folder_size: i64 = entry
            .get_item("folder_size")?
            .and_then(|v| v.extract().ok())
            .unwrap_or(0);

        let mut reasons: Vec<String> = Vec::new();

        if has_subtitle && subtitle == "无字" {
            reasons.push("Subtitle upgrade (中字 found, replacing 无字)".to_string());
        }

        let existing_prio = uncensored_sensor_priority(&sensor);
        if existing_prio > 0 && inferred_new_prio > existing_prio {
            reasons.push(format!("Sensor upgrade (无码 > {})", sensor));
        }

        if reasons.is_empty() {
            continue;
        }

        let category = if has_subtitle { "中字" } else { "无字" };
        let mut new_cat = category.to_string();
        if hacked {
            new_cat.push_str("-破解");
        }

        let rec = PyDict::new_bound(py);
        rec.set_item("video_code", as_upper(video_code))?;
        rec.set_item("existing_sensor", sensor)?;
        rec.set_item("existing_subtitle", subtitle)?;
        rec.set_item("existing_gdrive_path", folder_path)?;
        rec.set_item("existing_folder_size", folder_size)?;
        rec.set_item("new_torrent_category", new_cat)?;
        rec.set_item("deletion_reason", reasons.join("; "))?;
        rec.set_item("detect_datetime", now_str.clone())?;
        rec.set_item("is_deleted", "False")?;
        rec.set_item("delete_datetime", "")?;
        out.push(rec.into());
    }

    Ok(out)
}

/// Thin PyO3 wrapper around [`analyze_duplicates_core`].
///
/// Input: a list of folder dicts (each with `sensor_category`, `subtitle_category`,
/// `size`). Each folder is identified by its position in the list — Python uses
/// that index to reattach the original `FolderInfo` object.
///
/// Output: a dict with two keys:
///   - `keep`: `List[int]` — input indices to keep.
///   - `delete`: `List[dict]` — one per deletion, each with `index: int`,
///     `rule: str` ("Rule1" | "Rule2_youma" | "Rule2_wuma"), and the rule's
///     referenced sensors so Python can rebuild the exact reason string:
///       - Rule1       → `keep_sensor`, `loser_sensor`
///       - Rule2_youma → `category_name`
///       - Rule2_wuma  → `kept_zhongzi_sensor`
///
/// D5 invariant violations surface as a `ValueError`.
#[pyfunction]
pub fn analyze_folder_dedup<'py>(
    py: Python<'py>,
    folders: Vec<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    let mut parsed: Vec<DedupFolder> = Vec::with_capacity(folders.len());
    for (index, entry) in folders.iter().enumerate() {
        let sensor: String = entry
            .get_item("sensor_category")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing sensor_category"))?
            .extract()?;
        let subtitle: String = entry
            .get_item("subtitle_category")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing subtitle_category"))?
            .extract()?;
        let size: i64 = entry
            .get_item("size")?
            .and_then(|v| v.extract().ok())
            .unwrap_or(0);
        parsed.push(DedupFolder {
            index,
            sensor,
            subtitle,
            size,
        });
    }

    let decision = analyze_duplicates_core(&parsed)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let out = PyDict::new_bound(py);
    out.set_item("keep", decision.keep)?;

    let deletes = PyList::empty_bound(py);
    for d in &decision.delete {
        let rec = PyDict::new_bound(py);
        rec.set_item("index", d.index)?;
        match &d.rule {
            DeleteRule::SensorPriority {
                keep_sensor,
                loser_sensor,
            } => {
                rec.set_item("rule", "Rule1")?;
                rec.set_item("keep_sensor", keep_sensor)?;
                rec.set_item("loser_sensor", loser_sensor)?;
            }
            DeleteRule::SubtitleYouma { category_name } => {
                rec.set_item("rule", "Rule2_youma")?;
                rec.set_item("category_name", category_name)?;
            }
            DeleteRule::SubtitleWuma {
                kept_zhongzi_sensor,
            } => {
                rec.set_item("rule", "Rule2_wuma")?;
                rec.set_item("kept_zhongzi_sensor", kept_zhongzi_sensor)?;
            }
        }
        deletes.append(rec)?;
    }
    out.set_item("delete", deletes)?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folder(index: usize, sensor: &str, subtitle: &str, size: i64) -> DedupFolder {
        DedupFolder {
            index,
            sensor: sensor.to_string(),
            subtitle: subtitle.to_string(),
            size,
        }
    }

    /// Convenience: keep-index set + delete-index set for order-insensitive checks.
    fn keep_set(d: &DedupDecision) -> std::collections::BTreeSet<usize> {
        d.keep.iter().copied().collect()
    }
    fn delete_set(d: &DedupDecision) -> std::collections::BTreeSet<usize> {
        d.delete.iter().map(|x| x.index).collect()
    }

    const MB: i64 = 1024 * 1024;

    #[test]
    fn test_single_folder_keeps_all() {
        let f = vec![folder(0, "有码", "中字", 100 * MB)];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert!(d.delete.is_empty());
    }

    #[test]
    fn test_empty_keeps_all() {
        let d = analyze_duplicates_core(&[]).unwrap();
        assert!(d.keep.is_empty());
        assert!(d.delete.is_empty());
    }

    #[test]
    fn test_youma_zhongzi_over_wuzi() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
        assert!(matches!(
            d.delete[0].rule,
            DeleteRule::SubtitleYouma { .. }
        ));
    }

    #[test]
    fn test_wuma_liuchu_over_wuma() {
        let f = vec![
            folder(0, "无码流出", "无字", 100 * MB),
            folder(1, "无码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
        match &d.delete[0].rule {
            DeleteRule::SensorPriority {
                keep_sensor,
                loser_sensor,
            } => {
                assert_eq!(keep_sensor, "无码流出");
                assert_eq!(loser_sensor, "无码");
            }
            other => panic!("expected SensorPriority, got {other:?}"),
        }
    }

    #[test]
    fn test_wuma_over_wuma_pojie() {
        let f = vec![
            folder(0, "无码", "无字", 100 * MB),
            folder(1, "无码破解", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    #[test]
    fn test_wuma_zhongzi_beats_wuzi() {
        let f = vec![
            folder(0, "无码流出", "无字", 100 * MB),
            folder(1, "无码", "中字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        // 中字 (idx 1) kept; 无字 (idx 0) deleted via Rule2_wuma.
        assert_eq!(keep_set(&d), [1].into_iter().collect());
        assert_eq!(delete_set(&d), [0].into_iter().collect());
        assert!(matches!(d.delete[0].rule, DeleteRule::SubtitleWuma { .. }));
    }

    #[test]
    fn test_youma_and_wuma_independent() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "无字", 100 * MB),
            folder(2, "无码", "中字", 100 * MB),
            folder(3, "无码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0, 2].into_iter().collect());
        assert_eq!(delete_set(&d), [1, 3].into_iter().collect());
    }

    #[test]
    fn test_complex_wuma_keeps_only_liuchu_zhongzi() {
        // 无码流出-中字, 无码流出-无字, 无码-中字, 无码破解-无字 → keep ONLY 无码流出-中字.
        let f = vec![
            folder(0, "无码流出", "中字", 100 * MB),
            folder(1, "无码流出", "无字", 100 * MB),
            folder(2, "无码", "中字", 100 * MB),
            folder(3, "无码破解", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1, 2, 3].into_iter().collect());
    }

    // ---- size exception boundary ----

    #[test]
    fn test_size_exception_wuzi_just_over_30_percent_kept() {
        // 100MB 中字 vs 150MB 无字 → 150 > 130 → keep both.
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "无字", 150 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0, 1].into_iter().collect());
        assert!(d.delete.is_empty());
    }

    #[test]
    fn test_size_exception_wuzi_exactly_30_percent_deleted() {
        // 104857600 中字 vs 136314880 无字; 104857600*1.30 == 136314880.0 exactly,
        // STRICT > is False → delete 无字.
        let zhongzi = 104_857_600i64;
        let wuzi = 136_314_880i64;
        assert_eq!((zhongzi as f64) * SIZE_THRESHOLD_RATIO, wuzi as f64);
        let f = vec![
            folder(0, "有码", "中字", zhongzi),
            folder(1, "有码", "无字", wuzi),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    #[test]
    fn test_size_exception_wuzi_smaller_deleted() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "无字", 80 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    #[test]
    fn test_size_exception_zero_zhongzi_no_exception() {
        // zhongzi_size == 0 → guard `zhongzi_size > 0` False → delete 无字.
        let f = vec![
            folder(0, "有码", "中字", 0),
            folder(1, "有码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    #[test]
    fn test_size_exception_wuma_category() {
        // wuma branch: 100MB 中字 vs 140MB 无字 → 140 > 130 → keep both.
        let f = vec![
            folder(0, "无码流出", "中字", 100 * MB),
            folder(1, "无码流出", "无字", 140 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0, 1].into_iter().collect());
        assert!(d.delete.is_empty());
    }

    // ---- D5 invariants ----

    #[test]
    fn test_never_purge_all_keeps_nonempty() {
        // Any two-folder input must leave at least one keep.
        let f = vec![
            folder(0, "无码破解", "无字", 100 * MB),
            folder(1, "无码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert!(!d.keep.is_empty());
    }

    #[test]
    fn test_unclassifiable_sensor_fails_closed() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "未知", "中字", 100 * MB),
        ];
        let err = analyze_duplicates_core(&f).unwrap_err();
        assert!(matches!(err, DedupError::UnclassifiableFolder { index: 1, .. }));
    }

    #[test]
    fn test_unclassifiable_subtitle_fails_closed() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "未知", 100 * MB),
        ];
        let err = analyze_duplicates_core(&f).unwrap_err();
        assert!(matches!(err, DedupError::UnclassifiableFolder { index: 1, .. }));
    }

    #[test]
    fn test_equal_priority_tie_keeps_first_in_input_order() {
        // Two 无码 (equal priority): STABLE sort keeps the FIRST in input order.
        let f = vec![
            folder(0, "无码", "无字", 100 * MB),
            folder(1, "无码", "无字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    #[test]
    fn test_partition_covers_every_folder() {
        let f = vec![
            folder(0, "有码", "中字", 100 * MB),
            folder(1, "有码", "无字", 100 * MB),
            folder(2, "无码流出", "中字", 100 * MB),
            folder(3, "无码", "无字", 100 * MB),
            folder(4, "无码破解", "中字", 100 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        let mut all: Vec<usize> = d.keep.clone();
        all.extend(d.delete.iter().map(|x| x.index));
        all.sort_unstable();
        assert_eq!(all, vec![0, 1, 2, 3, 4]);
    }

    #[test]
    fn test_wuma_size_exception_uses_winner_size_not_max() {
        // zhongzi group: 无码流出(100MB, priority winner) + 无码(200MB, loser).
        // wuzi: 无码流出(150MB). The size exception compares against the KEPT
        // winner's 100MB (150 > 1.30*100=130 → kept), NOT the max zhongzi 200MB
        // (under which 150 < 1.30*200=260 would delete it).
        let f = vec![
            folder(0, "无码流出", "中字", 100 * MB),
            folder(1, "无码", "中字", 200 * MB),
            folder(2, "无码流出", "无字", 150 * MB),
        ];
        let d = analyze_duplicates_core(&f).unwrap();
        assert_eq!(keep_set(&d), [0, 2].into_iter().collect());
        assert_eq!(delete_set(&d), [1].into_iter().collect());
    }

    // ---- D5 invariant guards exercised directly (the fail-closed arms) ----

    fn youma_del(index: usize) -> DeleteDecision {
        DeleteDecision {
            index,
            rule: DeleteRule::SubtitleYouma {
                category_name: SENSOR_YOUMA.to_string(),
            },
        }
    }

    #[test]
    fn test_check_partition_ok() {
        assert!(check_partition(&[0], &[youma_del(1)], 2).is_ok());
    }

    #[test]
    fn test_check_partition_detects_duplicate_index() {
        // index 0 appears in both keep and delete → disjointness violated.
        let err = check_partition(&[0], &[youma_del(0)], 2).unwrap_err();
        assert!(matches!(err, DedupError::PartitionViolation { .. }));
    }

    #[test]
    fn test_check_partition_detects_uncovered_index() {
        // keep=[0], no deletes, n=2 → index 1 is uncovered.
        let err = check_partition(&[0], &[], 2).unwrap_err();
        assert!(matches!(err, DedupError::PartitionViolation { .. }));
    }

    #[test]
    fn test_check_partition_detects_out_of_range_index() {
        // keep=[0, 2], n=2: right count, but 1 is missing and 2 is out of range,
        // so it must not equal 0..2.
        let err = check_partition(&[0, 2], &[], 2).unwrap_err();
        assert!(matches!(err, DedupError::PartitionViolation { .. }));
    }

    #[test]
    fn test_check_nonempty_keep_rejects_empty_for_nonempty_input() {
        assert!(matches!(
            check_nonempty_keep(&[], 2).unwrap_err(),
            DedupError::EmptyKeep
        ));
        // Empty input (n==0) legitimately has an empty keep set.
        assert!(check_nonempty_keep(&[], 0).is_ok());
    }

    #[test]
    fn test_check_single_sensor_winner_rejects_multiple() {
        assert!(check_single_sensor_winner(1, 1).is_ok());
        assert!(check_single_sensor_winner(0, 0).is_ok());
        assert!(matches!(
            check_single_sensor_winner(2, 0).unwrap_err(),
            DedupError::MultipleSensorWinners { .. }
        ));
        assert!(matches!(
            check_single_sensor_winner(0, 2).unwrap_err(),
            DedupError::MultipleSensorWinners { .. }
        ));
    }
}
