#!/bin/bash
# aes 手动同步：rsync 到宿主机 → 校验 → 替换 → 清理本地（增量，可重入）
set -e
LOCAL=/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_asap7_v2/aes
HOST=/mnt/hgfs/host_files/dataset/asap7/parsed_ml_dataset_asap7
LOG=/home/lyh/OpenROAD-flow-scripts/dataset_parser/logs/sync_aes.log
log() { echo "$(date '+%F %T') $*" | tee -a $LOG; }

if [ ! -d "$LOCAL" ]; then
  log "本地 aes 不存在（可能已完成），检查宿主机是否已替换"
  [ -d "$HOST/aes" ] && log "宿主机 aes 存在" || log "宿主机 aes 缺失！"
  exit 0
fi
log "开始同步 aes（增量 rsync，已传文件自动跳过）"
rsync -a --delete "$LOCAL/" "$HOST/aes.new/"
NLOCAL=$(find "$LOCAL" -type f | wc -l)
NHOST=$(find "$HOST/aes.new" -type f | wc -l)
log "文件数: 本地=$NLOCAL 宿主机=$NHOST"
if [ "$NLOCAL" != "$NHOST" ]; then
  log "文件数不一致，重试一次 rsync"
  rsync -a --delete "$LOCAL/" "$HOST/aes.new/"
  NHOST=$(find "$HOST/aes.new" -type f | wc -l)
  [ "$NLOCAL" != "$NHOST" ] && { log "仍不一致，中止"; exit 1; }
fi
log "校验一致，替换宿主机 aes"
rm -rf "$HOST/aes"
mv "$HOST/aes.new" "$HOST/aes"
log "宿主机 aes 已替换"
rm -rf "$LOCAL"
log "本地 aes 已删，完成"
df -h / | tail -1 >> $LOG
