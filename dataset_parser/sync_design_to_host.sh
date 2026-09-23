#!/bin/bash
# 用法: sync_design_to_host.sh <design>
# 增量 rsync 到宿主机 → 文件数校验 → 替换旧目录 → 删除本地副本（可重入）
set -e
D=$1
if [ -z "$D" ]; then echo "用法: $0 <design>"; exit 1; fi
LOCAL=/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_asap7_v2/$D
HOST=/mnt/hgfs/host_files/dataset/asap7/parsed_ml_dataset_asap7
LOG=/home/lyh/OpenROAD-flow-scripts/dataset_parser/logs/sync_${D}.log
log() { echo "$(date '+%F %T') $*" | tee -a $LOG; }

if [ ! -d "$LOCAL" ]; then
  log "本地 $D 不存在（可能已完成），检查宿主机"
  [ -d "$HOST/$D" ] && log "宿主机 $D 存在，无需操作" || log "宿主机 $D 缺失！需人工检查"
  exit 0
fi
log "开始同步 $D（增量 rsync）"
rsync -a --delete "$LOCAL/" "$HOST/$D.new/"
NLOCAL=$(find "$LOCAL" -type f | wc -l)
NHOST=$(find "$HOST/$D.new" -type f | wc -l)
log "文件数: 本地=$NLOCAL 宿主机=$NHOST"
if [ "$NLOCAL" != "$NHOST" ]; then
  log "不一致，重试一次"
  rsync -a --delete "$LOCAL/" "$HOST/$D.new/"
  NHOST=$(find "$HOST/$D.new" -type f | wc -l)
  [ "$NLOCAL" != "$NHOST" ] && { log "仍不一致，中止"; exit 1; }
fi
log "校验一致，替换宿主机 $D"
rm -rf "$HOST/$D"
mv "$HOST/$D.new" "$HOST/$D"
log "宿主机 $D 已替换"
rm -rf "$LOCAL"
log "本地 $D 已删，完成"
df -h / | tail -1 >> $LOG
