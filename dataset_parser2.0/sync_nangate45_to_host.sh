#!/bin/bash
# nangate45 数据集同步（源文件 + 解析后向量）→ 宿主机，可重入、增量。
#
# 传 3 样（均只含 61 run，剔除清单与 sweep_nangate45.py 的 EXCLUDED 一致）：
#   1) flow/results/nangate45/  （flow 原始结果，26 GB）
#   2) flow/reports/nangate45/  （ml_reports 报告，28 GB）
#   3) flow/datasets/nangate45/ （解析后向量 JSONL+zstd，9.8 GB）→ 校验后删除本地副本
#
# 目标（共享文件夹挂载点 /mnt/hgfs/<share>，宿主机对应 E:\CTS 等）：
#   dataset/nangate45/runs_nangate45/{results,reports}/     # 源文件
#   dataset/nangate45/parsed_ml_dataset_nangate45/          # 解析后
set -e
ROOT=/home/lyh/OpenROAD-flow-scripts
SHARE=${HGFS_SHARE:-host_files}
MOUNT=/mnt/hgfs/$SHARE
BASE=$MOUNT/dataset/nangate45
LOG=$ROOT/dataset_parser/logs/sync_nangate45.log
log() { echo "$(date '+%F %T') $*" | tee -a $LOG; }

# 61 run：design 与其保留的 variant（与 sweep EXCLUDED 一致）
DESIGNS="aes blabla gcd picorv32 PPU s35932 salsa20"
VARIANTS="base util60_den60 util70_den70 util75_den72 low_density_den50 high_density_den76 route_adjust25 route_adjust60 route_adjust75 cts_small_cluster"
EXCLUDED="blabla/util60_den60 blabla/util70_den70 blabla/util75_den72 aes/cts_small_cluster blabla/cts_small_cluster gcd/cts_small_cluster PPU/cts_small_cluster s35932/cts_small_cluster salsa20/cts_small_cluster"

run_list() {  # 输出 61 行 "<design>/<variant>"
  for d in $DESIGNS; do
    for v in $VARIANTS; do
      case " $EXCLUDED " in *" $d/$v "*) ;; *) echo "$d/$v" ;; esac
    done
  done
}

rsync_dir() {  # $1=本地目录 $2=目标目录 $3=标签
  local src=$1 dst=$2 label=$3
  log "[$label] 开始 rsync（增量）"
  mkdir -p "$dst"
  rsync -a --delete "$src/" "$dst/"
  local n1 n2
  n1=$(find "$src" -type f | wc -l); n2=$(find "$dst" -type f | wc -l)
  log "[$label] 文件数: 本地=$n1 目标=$n2"
  if [ "$n1" != "$n2" ]; then
    rsync -a --delete "$src/" "$dst/"
    n2=$(find "$dst" -type f | wc -l)
    [ "$n1" != "$n2" ] && { log "[fail][$label] 文件数不一致 $n1 vs $n2"; return 1; }
  fi
  log "[$label][ok] $(du -sm "$dst" | cut -f1) MB"
}

main() {
  if [ ! -d "$MOUNT" ] || ! ls "$MOUNT" >/dev/null 2>&1; then
    log "[fail] 共享文件夹未挂载: $MOUNT"; exit 1
  fi
  log "=== 同步开始: nangate45 61 run ==="

  # 1) results（镜像 bench_<design>/<variant>，只含 61 run）
  run_list | while IFS=/ read -r d v; do
    src=$ROOT/flow/results/nangate45/bench_$d/$v
    [ -d "$src" ] && rsync_dir "$src" "$BASE/runs_nangate45/results/bench_$d/$v" "results/$d/$v"
  done

  # 2) reports（镜像 bench_<design>/<variant>）
  run_list | while IFS=/ read -r d v; do
    src=$ROOT/flow/reports/nangate45/bench_$d/$v
    [ -d "$src" ] && rsync_dir "$src" "$BASE/runs_nangate45/reports/bench_$d/$v" "reports/$d/$v"
  done

  # 3) datasets：整体同步 → 校验 → 删本地
  local local_ds=$ROOT/flow/datasets/nangate45
  if [ -d "$local_ds" ]; then
    rsync_dir "$local_ds" "$BASE/parsed_ml_dataset_nangate45" "datasets"
    log "[datasets] 宿主机校验通过，删除本地副本"
    rm -rf "$local_ds"
    log "[clean] 本地 datasets 已删除，VM 剩余 $(df -BG /home/lyh | tail -1 | awk '{print $4}')"
  else
    log "[datasets] 本地已不存在（可能已同步完成）"
    [ -d "$BASE/parsed_ml_dataset_nangate45" ] || log "[warn] 宿主机侧也缺失！"
  fi
  log "=== 同步完成 ==="
}

main "$@"
