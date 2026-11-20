#!/bin/bash

# --- 変更点1: 括弧を一切含まない退避用文字列 ---
TMP_L="__VIM_BRACE_L__"
TMP_R="__VIM_BRACE_R__"

for file in "$@"; do
  if [ -f "$file" ]; then
    echo "Processing: $file"

    # --- 1. 退避処理 ---
    # 変更点2: 行内に // があればそれ以降の部分を抽出し、
    # その中の { と } をすべて置換する（/e オプションによる安全で確実な処理）
    perl -i -pe "s!(//.*)! my \$c = \$1; \$c =~ s/\{/$TMP_L/g; \$c =~ s/\}/$TMP_R/g; \$c !e" "$file"

    # --- 2. Vimによる整形 ---
    vim -u NONE \
        -c "set expandtab sw=2 ts=2" \
        -c "set cindent" \
        -c "filetype indent on" \
        -c "normal gg=G" \
        -c "wq" \
        "$file"

    # --- 3. 復元処理 ---
    sed -i "s|$TMP_L|{|g" "$file"
    sed -i "s|$TMP_R|}|g" "$file"

  else
    echo "Skip: $file (not a file)"
  fi
done
echo "Done!"
