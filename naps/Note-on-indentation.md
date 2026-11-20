# Note on indentation based on vim

## `~/.vim/vimrc`
```
" ファイルタイプごとのインデント検出を有効にする
filetype plugin indent on

" インデントの設定（2スペース）
set expandtab      " タブをスペースに展開
set tabstop=2      " タブを2文字分として表示
set shiftwidth=2   " 自動インデントや '=' で入るスペースを2にする
set softtabstop=2  " 編集中のタブ入力を2スペース分にする
```

## Apply vim indentation
```
util/vindent.sh [file1] [file2] ...
```


