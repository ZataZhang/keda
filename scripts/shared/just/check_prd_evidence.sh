#!/usr/bin/env bash
# scripts/shared/just/check_prd_evidence.sh
# Final gate for `just ai implement`. Two checks:
#   1. 证据目录里的每张静态图都必须在证据报告里用 `![](…)` 就地嵌入（所有 PRD）。
#   2. PRD 触及前端 app 时，证据目录至少要有一张截图或一段录屏。

set -euo pipefail

prd_path="${1:-}"
worktree_root="${2:-}"

if [ -z "$prd_path" ]; then
    echo "Usage: $0 <prd-path> [worktree-root]"
    exit 1
fi

if [ ! -f "$prd_path" ]; then
    echo "ERROR: PRD file not found: $prd_path"
    exit 1
fi

if [ -n "$worktree_root" ]; then
    search_root="$worktree_root"
else
    search_root="$(git rev-parse --show-toplevel)"
fi

prd_basename="$(basename "$prd_path" .md)"
evidence_dir="$search_root/tasks/evidence/$prd_basename"
evidence_report_path="$evidence_dir/$prd_basename.evidence-report.md"

# 一次 find 收齐视觉证据，并按能否内联渲染分成两拨：静态图要求就地嵌图，
# 录屏无法在 Markdown 里内联，只参与前端证据存在性检查。
visual_files=()
still_image_files=()
if [ -d "$evidence_dir" ]; then
    while IFS= read -r -d '' visual_file_path; do
        visual_files+=("$visual_file_path")
        case "$(printf '%s' "$visual_file_path" | tr '[:upper:]' '[:lower:]')" in
            *.png | *.jpg | *.jpeg) still_image_files+=("$visual_file_path") ;;
        esac
    done < <(find "$evidence_dir" -maxdepth 1 -type f \
        \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webm' \) \
        -print0 2>/dev/null || true)
fi

# --- Check 1: 静态图必须在证据报告里就地嵌入 ---
# 只在报告里写一行 `open <路径>` 不算呈递：人审时要在报告里直接看到图。报告与
# 图片同目录，`![说明](图片名.png)` 在任何本地 Markdown 预览里都能渲染。图片被
# .gitignore 排除、在 GitHub 上显示为坏图，是那句「本地图片」标注要解释的事，
# 不是不嵌图的理由——嵌图、标注、open 命令三者并存，后两者不替代第一项。
check_still_images_embedded() {
    if [ ! -f "$evidence_report_path" ] || [ "${#still_image_files[@]}" -eq 0 ]; then
        return 0
    fi

    # 按 basename 比对报告里的 Markdown 图片目标，避开相对路径写法与正则转义两处
    # 坑：`![x](./rv-1.png "标题")` 与 `![x](rv-1.png)` 视为同一张图。
    local embedded_image_basenames=()
    local markdown_image_tag
    local embedded_image_target
    while IFS= read -r markdown_image_tag; do
        embedded_image_target="${markdown_image_tag#*](}"
        embedded_image_target="${embedded_image_target%)}"
        embedded_image_target="${embedded_image_target%% *}"
        embedded_image_basenames+=("$(basename "$embedded_image_target")")
    done < <(grep -oE '!\[[^]]*\]\([^)]+\)' "$evidence_report_path" || true)

    local unembedded_image_basenames=()
    local still_image_basename
    local embedded_image_basename
    local is_embedded
    for still_image_path in "${still_image_files[@]}"; do
        still_image_basename="$(basename "$still_image_path")"
        is_embedded=false
        for embedded_image_basename in ${embedded_image_basenames[@]+"${embedded_image_basenames[@]}"}; do
            if [ "$embedded_image_basename" = "$still_image_basename" ]; then
                is_embedded=true
                break
            fi
        done
        if [ "$is_embedded" != "true" ]; then
            unembedded_image_basenames+=("$still_image_basename")
        fi
    done

    if [ "${#unembedded_image_basenames[@]}" -gt 0 ]; then
        echo "ERROR: 下列证据图片没有在证据报告里就地嵌入：$evidence_report_path"
        printf '   - %s\n' "${unembedded_image_basenames[@]}"
        echo "       用 ![<说明>](<图片名>) 嵌进人审导航（与报告同目录，相对路径即可）。"
        echo "       open 命令与「本地图片不入 Git」标注是嵌图的补充，不能替代嵌图。"
        return 1
    fi

    echo "✅ 证据报告已就地嵌入全部 ${#still_image_files[@]} 张证据图片。"
    return 0
}

if ! check_still_images_embedded; then
    exit 1
fi

# Determine whether this PRD touches frontend apps.
# Priority 0: an explicit "No frontend impact" declaration in the PRD — trust
# git diff, not text mentions.
# Priority 1: inspect the PRD Change Impact Tree for frontend paths.
# Priority 2: if the PRD has no explicit tree, fall back to git diff against main.
touches_frontend=false

if grep -qiE 'No frontend impact' "$prd_path"; then
    # Backend-only PRD 会在 non-goals/兼容说明中引用前端路径；文字引用不等于变更。
    if git -C "$search_root" diff --name-only HEAD -- frontend-admin frontend-public 2>/dev/null \
        | grep -q .; then
        touches_frontend=true
    fi
elif grep -qE 'frontend-admin/|frontend-public/' "$prd_path"; then
    touches_frontend=true
fi

if [ "$touches_frontend" != "true" ] && [ -z "$worktree_root" ]; then
    # Fallback: check whether the current branch has modified frontend files.
    if git diff --name-only HEAD >/dev/null 2>&1; then
        if git diff --name-only HEAD | grep -qE '^(frontend-admin|frontend-public)/'; then
            touches_frontend=true
        fi
    fi
fi

if [ "$touches_frontend" != "true" ]; then
    echo "✅ No frontend changes detected for $prd_basename; visual evidence not required."
    exit 0
fi

if [ ! -d "$evidence_dir" ]; then
    echo "ERROR: Frontend changes detected but evidence directory is missing: $evidence_dir"
    echo "       Run Playwright e2e tests and copy screenshots/videos into that directory."
    exit 1
fi

if [ "${#visual_files[@]}" -eq 0 ]; then
    echo "ERROR: Frontend changes detected but no visual evidence (.png/.jpg/.webm) found in $evidence_dir"
    echo "       Run Playwright e2e tests and copy screenshots/videos into that directory."
    exit 1
fi

echo "✅ Frontend changes detected and visual evidence found:"
printf '   - %s\n' "$(basename -a "${visual_files[@]}")"
exit 0
