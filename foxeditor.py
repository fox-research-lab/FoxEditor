"""
FoxEditor v0.6 - viライクなGUIテキストエディタ
Windows上で動作するPython/tkinter製の軽量エディタ

起動方法:
    python foxeditor.py           # 空バッファで起動
    python foxeditor.py memo.txt  # ファイルを開いて起動

v0.5 新機能:
    :gemma4_smr  ローカルOllama上のGemma4:e2bによるコード要約

v0.6 新機能:
    0          Normalモードで行頭移動 (count入力前のみ; 入力中は桁追加)
    $          Normalモードで行末移動
    o          Normalモードで下行挿入してInsertモードへ移行
    u          Normalモードでundo
    :set num   行番号表示ON (LineNumberAreaクラスによる実装)
"""

import tkinter as tk
from tkinter import font as tkfont
import sys
import os
import threading
import json

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

# ============================================================
# カラーテーマ定数
# ============================================================
COLOR_BG            = "#000000"   # 背景色: 黒
COLOR_FG            = "#ff9900"   # テキスト文字色: オレンジ
COLOR_BORDER_NORMAL = "#00ff66"   # Normalモード枠線: 緑
COLOR_BORDER_INSERT = "#ff3030"   # Insertモード枠線: 赤
COLOR_STATUS_BG     = "#111111"   # ステータスバー背景: 濃いグレー
COLOR_CMD_BG        = "#0a0a0a"   # コマンド入力欄背景
COLOR_SEARCH_HIT_BG = "#00cc44"   # v0.2 検索ヒット背景: 緑
COLOR_SEARCH_HIT_FG = "#000000"   # v0.2 検索ヒット文字色: 黒

# ============================================================
# モード定数
# ============================================================
MODE_NORMAL  = "NORMAL"
MODE_INSERT  = "INSERT"
MODE_COMMAND = "COMMAND"
MODE_SEARCH  = "SEARCH"   # v0.2 viライク検索モード

# ============================================================
# AI設定定数
# ============================================================
AI_API_URL = "http://localhost:11434/v1/chat/completions"   # OpenAI互換API
AI_MODEL   = "gemma4:e2b"
AI_TIMEOUT = 300   # Ollama応答タイムアウト秒数 (大きなファイルでも余裕を持つ)


# ============================================================
# SearchManager クラス
# ============================================================
class SearchManager:
    """
    FoxEditor の検索機能を一手に担うクラス。
    検索状態 (検索語・一致位置・現在インデックス) を自身で管理し、
    テキストエリアへのハイライト付与・カーソルジャンプを行う。
    FoxEditor の機能には self.editor 経由でアクセスする。
    """

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。text_area / cmd_var /
                    status_message / _set_mode() などを借用する。
        """
        self.editor = editor

        self.search_word    = ""   # 直前の検索文字列
        self.search_matches = []   # 一致位置一覧 [(start_index, end_index), ...]
        self.search_index   = -1   # search_matches 内の現在位置 (-1 は未検索)

    # ----------------------------------------------------------
    # 公開メソッド (FoxEditor のキーテーブル・コマンドから呼ばれる)
    # ----------------------------------------------------------

    def start(self):
        """/ キー: 検索入力モードへ移行する。"""
        self.editor.status_message = ""
        self.editor._set_mode(MODE_SEARCH)

    def execute(self):
        """
        Enter キー: 検索を実行して最初の一致へジャンプする。

        処理フロー:
          1. 入力文字列を取得する
          2. 既存ハイライトを消去する
          3. _collect_matches() で全一致位置を収集する
          4. _highlight_search_results() で全一致箇所をハイライトする
          5. 最初の一致へカーソルを移動する
          6. Normal モードへ戻る
        """
        word = self.editor.cmd_var.get()

        # 空文字列は検索しない
        if not word:
            self.editor._set_mode(MODE_NORMAL)
            return

        # 検索文字列を保存し、前回のハイライトを消去してから検索する
        self.search_word = word
        self.clear_highlight()
        self.search_matches = self._collect_matches(word)

        if not self.search_matches:
            # 一致箇所なし
            self.search_index = -1
            self.editor.status_message = f"検索結果なし: {word}"
            self.editor._set_mode(MODE_NORMAL)
            return

        # 全一致箇所をハイライト表示する
        self._highlight_search_results()

        # 最初の一致箇所へカーソルを移動する
        self.search_index = 0
        self._jump_to_match(self.search_index)
        self.editor._set_mode(MODE_NORMAL)

    def next(self):
        """
        n キー: 次の検索一致箇所へ移動する。
        末尾まで到達したら先頭へ折り返す (循環)。
        """
        if not self.search_matches:
            self.editor.status_message = "検索文字列がありません。/ で検索してください"
            self.editor._update_status()
            return

        self.search_index = (self.search_index + 1) % len(self.search_matches)
        self._jump_to_match(self.search_index)
        self.editor._update_status()

    def prev(self):
        """
        N キー: 前の検索一致箇所へ移動する。
        先頭まで到達したら末尾へ折り返す (循環)。
        """
        if not self.search_matches:
            self.editor.status_message = "検索文字列がありません。/ で検索してください"
            self.editor._update_status()
            return

        self.search_index = (self.search_index - 1) % len(self.search_matches)
        self._jump_to_match(self.search_index)
        self.editor._update_status()

    def clear_highlight(self):
        """search_hit タグをテキスト全体から取り除きハイライトを消去する。"""
        try:
            self.editor.text_area.tag_remove("search_hit", "1.0", tk.END)
        except Exception:
            pass

    # ----------------------------------------------------------
    # 内部メソッド (クラス外からは呼ばない)
    # ----------------------------------------------------------

    def _collect_matches(self, word: str) -> list:
        """
        テキスト全体から word の全一致位置を収集して返す。
        tkinter Text.search() をループさせて全件を取得する。

        Args:
            word: 検索する文字列

        Returns:
            [(start_index, end_index), ...] の一覧。一致なしなら空リスト。
        """
        matches = []
        count_var = tk.IntVar()
        start = "1.0"

        try:
            while True:
                pos = self.editor.text_area.search(
                    word,
                    start,
                    stopindex=tk.END,
                    count=count_var,
                    nocase=False,
                )
                if not pos:
                    # 一致なし → ループ終了
                    break
                length = count_var.get()
                if length == 0:
                    # ゼロ幅マッチは無限ループになるので打ち切る
                    break
                end = f"{pos}+{length}c"
                matches.append((pos, end))
                # 次の検索開始位置を一致末尾へ進める
                start = end
        except Exception:
            pass

        return matches

    def _highlight_search_results(self):
        """search_matches の全一致箇所へ search_hit タグを付与する。"""
        for start, end in self.search_matches:
            try:
                self.editor.text_area.tag_add("search_hit", start, end)
            except Exception:
                pass

    def _jump_to_match(self, index: int):
        """
        search_matches[index] の位置へカーソルを移動し画面内へスクロールする。
        ステータスバーに現在位置 [n/total] を表示する。

        Args:
            index: search_matches のインデックス
        """
        if not self.search_matches:
            return

        try:
            start, _ = self.search_matches[index]
            # カーソルを一致箇所先頭へ移動する
            self.editor.text_area.mark_set(tk.INSERT, start)
            # 一致箇所が画面外なら見える位置へスクロールする
            self.editor.text_area.see(start)
            total = len(self.search_matches)
            self.editor.status_message = (
                f"検索: {self.search_word}  [{index + 1}/{total}]"
            )
        except Exception:
            pass


# ============================================================
# EditCommands クラス
# ============================================================
class EditCommands:
    """
    FoxEditor の行編集機能 (dd / yy / p) を担当するクラス。
    pending_key / yank_buffer の状態管理と行の切り取り・コピー・貼り付けを行う。
    FoxEditor の機能には self.editor 経由でアクセスする。
    SearchManager と同じ設計パターンで実装している。
    """

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。text_area / status_message /
                    modified / _update_status() などを借用する。
        """
        self.editor = editor

        self.pending_key = ""   # 2キーコマンド待機中キー ("d" / "y" / "")
        self.yank_buffer = ""   # dd / yy でコピーした行テキスト

    # ----------------------------------------------------------
    # 公開メソッド (FoxEditor のキーハンドラから呼ばれる)
    # ----------------------------------------------------------

    def clear_pending_key(self):
        """pending_key をリセットする。"""
        self.pending_key = ""

    def delete_current_line(self):
        """
        dd コマンド: 現在行を yank_buffer に保存してから削除する。
        行末改行も含めて切り取る。最終行で改行がない場合も安全に処理する。
        """
        start, end = self._get_current_line_range()
        self.yank_buffer = self.editor.text_area.get(start, end)
        self.editor.text_area.delete(start, end)
        self.editor.modified = True
        self.editor.status_message = "1行切り取りました"
        self.clear_pending_key()
        self.editor._update_status()

    def yank_current_line(self):
        """
        yy コマンド: 現在行を yank_buffer にコピーする (削除しない)。
        """
        start, end = self._get_current_line_range()
        self.yank_buffer = self.editor.text_area.get(start, end)
        self.editor.status_message = "1行コピーしました"
        self.clear_pending_key()
        self.editor._update_status()

    def paste_line_below(self):
        """
        p コマンド: yank_buffer の内容を現在行の下に貼り付ける。
        yank_buffer が空の場合はステータスバーにメッセージを表示する。
        """
        if not self.yank_buffer:
            self.editor.status_message = "貼り付ける行がありません"
            self.editor._update_status()
            return

        # 貼り付けるテキスト (末尾の改行を除いた行内容)
        content = self.yank_buffer.rstrip("\n")

        # 現在行の行末 (改行文字の前) に "\n + content" を挿入する
        current_row = int(self.editor.text_area.index(tk.INSERT).split(".")[0])
        line_end = self.editor.text_area.index(f"{current_row}.end")
        self.editor.text_area.insert(line_end, "\n" + content)

        # カーソルを貼り付けた行の先頭へ移動する
        new_row = current_row + 1
        self.editor.text_area.mark_set(tk.INSERT, f"{new_row}.0")
        self.editor.text_area.see(tk.INSERT)

        self.editor.modified = True
        self.editor.status_message = "貼り付けました"
        self.editor._update_status()

    # ----------------------------------------------------------
    # 内部メソッド (クラス外からは呼ばない)
    # ----------------------------------------------------------

    def _get_current_line_range(self) -> tuple:
        """
        現在行の範囲を (start_index, end_index) で返す。
        通常行は行末の改行文字まで含む。最終行で改行がない場合は行末まで。

        Returns:
            (start, end): tkinter Text ウィジェットのインデックス文字列のタプル
        """
        cursor = self.editor.text_area.index(tk.INSERT)
        # "3.5" のような "行.列" 形式の文字列から、行番号 "3" だけを取り出している
        row = cursor.split(".")[0]
        start = f"{row}.0"
        line_end = self.editor.text_area.index(f"{row}.end")
        # 行末の次の文字が改行かどうかで範囲を決める
        next_char = self.editor.text_area.get(line_end, f"{line_end}+1c")
        if next_char == "\n":
            end = f"{line_end}+1c"
        else:
            end = line_end
        return start, end


# ============================================================
# FileManager クラス
# ============================================================
class FileManager:
    """
    FoxEditor のファイル入出力機能を担当するクラス。
    ファイルの読み込み・保存処理を一手に担い、
    FoxEditor の機能には self.editor 経由でアクセスする。
    SearchManager / EditCommands と同じ設計パターンで実装している。
    """

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。text_area / filepath /
                    modified / status_message / _update_status() などを借用する。
        """
        self.editor = editor

    # ----------------------------------------------------------
    # 公開メソッド (FoxEditor のコマンドテーブル・初期化から呼ばれる)
    # ----------------------------------------------------------

    def load_file(self, filepath: str):
        """
        指定されたファイルを読み込んでテキストエリアに表示する。
        UTF-8 で読み込みを試み、失敗した場合は CP932 で再試行する。
        ファイルが存在しない場合は新規ファイルとして扱い、エラーにしない。
        読み込み失敗時はステータスバーにエラー内容を表示し、アプリは落とさない。

        Args:
            filepath: 読み込むファイルのパス
        """
        if not os.path.exists(filepath):
            # 存在しないファイル → 新規作成扱い
            self.editor.status_message = f"新規ファイル: {os.path.basename(filepath)}"
            return

        try:
            # まず UTF-8 で試みる
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self._insert_content(content)
            self.editor.status_message = f"読み込み完了: {os.path.basename(filepath)}"

        except UnicodeDecodeError:
            # UTF-8 で失敗したら CP932 (Shift-JIS) で再試行
            try:
                with open(filepath, "r", encoding="cp932") as f:
                    content = f.read()
                self._insert_content(content)
                self.editor.status_message = f"読み込み完了 (CP932): {os.path.basename(filepath)}"
            except Exception as e:
                self.editor.status_message = f"[エラー] ファイル読み込み失敗: {e}"

        except OSError as e:
            self.editor.status_message = f"[エラー] ファイルを開けません: {e}"
        except Exception as e:
            self.editor.status_message = f"[エラー] 予期しないエラー: {e}"

    def save_file(self) -> bool:
        """
        現在のテキストエリアの内容をファイルに保存する。
        Text ウィジェットの末尾改行を除去して保存する。
        失敗時はステータスバーにエラー内容を表示し、アプリは落とさない。

        Returns:
            True: 保存成功 / False: 保存失敗またはファイルパス未設定
        """
        if not self.editor.filepath:
            # ファイルパス未設定 (引数なし起動の新規バッファ)
            self.editor.status_message = "[エラー] ファイル名が未設定です。引数付きで起動してください"
            return False

        try:
            # tkinter の Text ウィジェットは末尾に自動で改行を付けるので除去する
            content = self.editor.text_area.get("1.0", tk.END)
            if content.endswith("\n"):
                content = content[:-1]

            with open(self.editor.filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.editor.modified = False
            self.editor.status_message = f"保存しました: {os.path.basename(self.editor.filepath)}"
            return True

        except OSError as e:
            self.editor.status_message = f"[エラー] 保存失敗 (OS エラー): {e}"
            return False
        except Exception as e:
            self.editor.status_message = f"[エラー] 保存中に予期しないエラー: {e}"
            return False

    # ----------------------------------------------------------
    # 内部メソッド (クラス外からは呼ばない)
    # ----------------------------------------------------------

    def _insert_content(self, content: str):
        """
        テキストエリア全体を content で置き換える共通処理。
        カーソルを 1.0 に戻し、modified を False にする。
        """
        self.editor.text_area.config(state=tk.NORMAL)
        self.editor.text_area.delete("1.0", tk.END)
        self.editor.text_area.insert("1.0", content)
        self.editor.text_area.mark_set(tk.INSERT, "1.0")
        self.editor.text_area.see(tk.INSERT)
        self.editor.modified = False


# ============================================================
# KeyDispatcher クラス
# ============================================================
class KeyDispatcher:
    """
    FoxEditor のキー入力処理を担当するクラス。
    Normal / Insert モードのキーイベントを受け取り、適切な処理へ振り分ける。
    FoxEditor の機能には self.editor 経由でアクセスする。
    SearchManager / EditCommands / FileManager と同じ設計パターンで実装している。
    """

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。mode / text_area / normal_key_table /
                    search / edit / _set_mode() / _update_status() などを借用する。
        """
        self.editor = editor

    # ----------------------------------------------------------
    # 公開メソッド (FoxEditor の _bind_keys から登録される)
    # ----------------------------------------------------------

    def on_key_press(self, event: tk.Event):
        """
        テキストエリアへのキー入力を処理するメインハンドラ。
        現在のモードに応じて Normal / Insert の処理メソッドへ委譲する。

        Returns:
            "break" を返すと tkinter のデフォルト処理をキャンセルする。
        """
        if self.editor.mode == MODE_NORMAL:
            return self._handle_normal_key(event)
        elif self.editor.mode == MODE_INSERT:
            return self._handle_insert_key(event)
        # COMMAND / SEARCH モードは cmd_entry が別ウィジェットなのでここには来ない
        return None

    # ----------------------------------------------------------
    # 内部メソッド (クラス外からは呼ばない)
    # ----------------------------------------------------------

    def _handle_normal_key(self, event: tk.Event):
        """
        Normalモードのキー操作を処理する。

        対応キー:
          h/j/k/l  : カーソル移動 (vi 方向キー)
          i        : Insert モードへ移行
          x        : カーソル位置の1文字削除
          :        : コマンド入力モードへ
          /        : 検索モードへ移行 (v0.2)
          n        : 次の検索一致箇所へ移動 (v0.2)
          N        : 前の検索一致箇所へ移動 (v0.2)
          dd       : 現在行を切り取り (v0.3)
          yy       : 現在行をコピー (v0.3)
          p        : 現在行の下に貼り付け (v0.3)
          Ctrl+D   : 半ページ下へスクロール (v0.33)
          Ctrl+U   : 半ページ上へスクロール (v0.33)
          0-9      : 数字バッファに追加 / 0単独で行頭移動 (v0.33/v0.6)
          G        : 最終行 / 数字G で指定行へジャンプ (v0.33)
          $        : 行末移動 (v0.6)
          o        : 下行挿入してInsertモードへ (v0.6)
          u        : Undo (v0.6)
        """
        char = event.char    # 入力された文字 ("h", ":", など)

        # 修飾キー単体 (Shift/Ctrl/Alt) は何もせず無視する
        # count_buffer はクリアしない (Shift+G の Shift を誤ってクリアしないため)
        _MODIFIER_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}
        if event.keysym in _MODIFIER_KEYS:
            return "break"

        # v0.33 Ctrl キー組み合わせを最優先で処理する
        # event.state & 0x4 が True のとき Ctrl キーが押されている
        if event.state & 0x4:
            if event.keysym == "d":
                self.editor._scroll_half_page(+15)
                self.editor._clear_count_buffer()
            elif event.keysym == "u":
                self.editor._scroll_half_page(-15)
                self.editor._clear_count_buffer()
            return "break"

        # v0.3 2キーコマンドの pending_key を先に処理する
        if self.editor.edit.pending_key:
            if self.editor.edit.pending_key == "d" and char == "d":
                self.editor.edit.delete_current_line()
            elif self.editor.edit.pending_key == "y" and char == "y":
                self.editor.edit.yank_current_line()
            else:
                # d/y の次に別キーが来たら pending をクリアして無視する
                self.editor.edit.clear_pending_key()
            self.editor._clear_count_buffer()
            return "break"

        # v0.33/v0.6 数字入力処理
        # vi同様: count_buffer が空の状態での "0" は行頭移動
        #         count_buffer に数字が入っている状態での "0" は桁追加
        if char.isdigit():
            if char == "0" and not self.editor.count_buffer:
                # count入力前の 0 → 行頭移動
                self.editor._move_line_start()
                self.editor._clear_count_buffer()
            else:
                # count入力中の 0、または 1-9 → count_bufferへ追加
                self.editor.count_buffer += char
                self.editor._update_status()
            return "break"

        # v0.33 G: count_buffer があれば指定行、なければ最終行へジャンプ
        # event.char は Shift+g で "G" になるが keysym でも補足する
        if char == "G" or event.keysym == "G":
            if self.editor.count_buffer:
                try:
                    self.editor._go_to_line(int(self.editor.count_buffer))
                except ValueError:
                    pass
            else:
                self.editor._go_to_last_line()
            self.editor._clear_count_buffer()
            return "break"

        # v0.3 dd/yy の1文字目: pending_key を設定して待機する
        if char == "d":
            self.editor.edit.pending_key = "d"
            return "break"

        if char == "y":
            self.editor.edit.pending_key = "y"
            return "break"

        # ディシジョンテーブルで残りのキーを処理する
        func = self.editor.normal_key_table.get(char)
        if func:
            func()
            self.editor._clear_count_buffer()
            return "break"

        # 未知のキーはすべて無視する (Normal モードは編集不可)
        self.editor._clear_count_buffer()
        return "break"

    def _handle_insert_key(self, event: tk.Event):
        """
        Insertモードのキー操作を処理する。

        Esc のみ特別処理し、それ以外は tkinter のデフォルト処理に委ねる
        (文字入力・Backspace・Enter はデフォルト動作で動く)。
        """
        if event.keysym == "Escape":
            # Esc で Normal モードへ戻る
            self.editor.status_message = ""
            self.editor.modified = True
            self.editor._set_mode(MODE_NORMAL)
            return "break"

        # その他はデフォルト処理 (文字挿入・Backspace・Enter 等)
        # modified フラグは後続の KeyRelease で更新される
        return None


# ============================================================
# AIManager クラス (v0.5 新規追加)
# ============================================================
class AIManager:
    """
    FoxEditor のAI機能を担当するクラス。
    ローカルOllama上のGemma4:e2bを使ってコード要約などを行う。

    通信はすべてdaemonスレッドで行い、tkinter mainloopをブロックしない。
    エラーはすべてキャッチしてステータスバーに表示し、アプリを落とさない。

    API接続先: http://localhost:11434/v1/chat/completions (OpenAI互換)
    使用モデル: gemma4:e2b (固定)
    """

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。text_area / status_message /
                    _update_status() などを借用する。
        """
        self.editor = editor

    # ----------------------------------------------------------
    # 公開メソッド (command_table から呼ばれる)
    # ----------------------------------------------------------

    def summarize_code(self):
        """
        :gemma4_smr コマンド: 現在のテキスト全体をGemma4:e2bで要約する。

        処理フロー:
          1. テキストエリアの内容を全取得する
          2. 要約プロンプトを生成する
          3. daemonスレッドでOllama APIへ送信する (GUIフリーズ防止)
          4. 結果をステータスバーに表示する (スレッド内でafter()経由)
        """
        # requestsが利用できない場合は即座にエラー表示
        if not _REQUESTS_AVAILABLE:
            self.editor.status_message = "[AIエラー] requestsライブラリが未インストールです (pip install requests)"
            self.editor._update_status()
            return

        # テキストエリアの内容を取得する
        try:
            cursor = self.editor.text_area.index(tk.INSERT)
            row    = int(cursor.split(".")[0])

            # カーソル行から最大500行を分析対象にする
            start = max(1, row)
            end   = row + 3000

            raw_text = self.editor.text_area.get(f"{start}.0", f"{end}.0")
        except Exception as e:
            self.editor.status_message = f"[AIエラー] テキスト取得失敗: {e}"
            self.editor._update_status()
            return

        # 空テキストは送信しない
        if not raw_text.strip():
            self.editor.status_message = "[AIエラー] テキストが空です"
            self.editor._update_status()
            return

        # 行番号をファイル実際の行番号で付与する
        # 例: "  58: class AIManager:"
        lines     = raw_text.splitlines()
        last_line = start + len(lines) - 1
        width     = len(str(last_line))   # 行番号の桁数を揃えるための幅
        numbered  = [f"{start + i:>{width}}: {line}" for i, line in enumerate(lines)]
        code_text = "\n".join(numbered)

        # 要約プロンプトを生成する
        prompt = self._build_summarize_prompt(code_text)

        # 処理中メッセージに分析行範囲を表示する
        self.editor.status_message = f"[AI処理中] 行{start}〜{last_line} を Gemma4:e2b で分析中..."
        self.editor._update_status()

        # GUIフリーズ防止: daemonスレッドでAPIリクエストを実行する
        threading.Thread(
            target=self._send_request,
            args=(prompt,),
            daemon=True
        ).start()

    # ----------------------------------------------------------
    # 内部メソッド (クラス外からは呼ばない)
    # ----------------------------------------------------------

    def _build_summarize_prompt(self, code_text: str) -> str:
        """
        コード要約用プロンプトを生成して返す。

        Args:
            code_text: 要約対象のテキスト全体

        Returns:
            Gemma4:e2b へ送信するプロンプト文字列
        """
        """
        return (
            "以下のコードを日本語で要約してください。\n"
            "次の3点を簡潔にまとめてください:\n"
            "1. コードの役割 (何をするプログラムか)\n"
            "2. 主なクラスや関数 (あれば)\n"
            "3. 処理概要 (どのように動くか)\n\n"
            "---コード---\n"
            f"{code_text}\n"
            "---ここまで---\n\n"
            "日本語で簡潔に答えてください。"
        )
        """
        return (
            "以下のPythonコードを"
            "ソフトウェア設計レビュー視点で解析してください。\n\n"

            "特に以下を重点的に説明してください:\n\n"

            "1. クラスごとの責務\n"
            "2. クラス同士の依存関係\n"
            "3. 各クラスがどのメソッドを通じて連携しているか\n"
            "4. イベント処理の流れ\n"
            "5. 状態管理の方法\n"
            "6. 設計上うまく分離されている点\n"
            "7. 将来的に肥大化しそうな箇所\n\n"

            "単なる概要説明ではなく、"
            "設計構造が分かるように説明してください。\n\n"

            "---コード---\n"
            f"{code_text}\n"
            "---ここまで---"
        )

    def _send_request(self, prompt: str):
        """
        Ollama API へリクエストを送信し、結果をステータスバーに反映する。
        このメソッドはdaemonスレッド内で実行される。

        tkinterはスレッドセーフでないため、GUI更新はroot.after()経由で行う。

        Args:
            prompt: Gemma4:e2b へ送信するプロンプト文字列
        """
        # Ollama ネイティブAPI (/api/generate) のリクエストボディ
        # OpenAI互換API (/v1/chat/completions) のリクエストボディ
        # gemma4:e2b はこのエンドポイントで動作実証済み (gemma4_e2b_e4b_test.py 参照)
        data = {
            "model": AI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,     # 要約は1000トークンあれば十分
            "temperature": 0.1,    # 低温で安定した出力にする
            "stream": False        # 一括取得 (OpenAI互換APIではこちらが安定)
        }

        try:
            # Ollama OpenAI互換APIへPOSTリクエストを送信する
            response = requests.post(
                AI_API_URL,
                json=data,
                timeout=AI_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )

            # HTTPエラーを確認する (4xx / 5xx)
            if response.status_code != 200:
                msg = f"[AIエラー] HTTP {response.status_code}: {response.text[:80]}"
                self._update_status_from_thread(msg)
                return

            # JSONをパースする
            try:
                result = response.json()
            except (json.JSONDecodeError, ValueError) as e:
                msg = f"[AIエラー] JSONパース失敗: {e}"
                self._update_status_from_thread(msg)
                return

            # OpenAI互換APIのレスポンスは choices[0]["message"]["content"] に入る
            if "choices" not in result or not result["choices"]:
                msg = f"[AIエラー] choicesキーがありません: {list(result.keys())}"
                self._update_status_from_thread(msg)
                return

            try:
                summary = result["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, TypeError) as e:
                msg = f"[AIエラー] レスポンス構造が想定外: {e}"
                self._update_status_from_thread(msg)
                return

            # AI応答が空の場合はエラー扱いにする
            if not summary:
                msg = "[AIエラー] AI応答が空です"
                self._update_status_from_thread(msg)
                return

            # ファイル書き込みとポップアップ表示はメインスレッドで行う
            self.editor.root.after(0, lambda: self._write_and_show_summary(summary))

        except requests.exceptions.ConnectionError:
            # Ollamaが未起動またはlocalhost接続失敗
            msg = f"[AIエラー] Ollama未起動または接続失敗 ({AI_API_URL})"
            self._update_status_from_thread(msg)

        except requests.exceptions.Timeout:
            # タイムアウト
            msg = f"[AIエラー] タイムアウト ({AI_TIMEOUT}秒) — Ollamaが応答しません"
            self._update_status_from_thread(msg)

        except requests.exceptions.RequestException as e:
            # その他のrequests例外
            msg = f"[AIエラー] 通信エラー: {e}"
            self._update_status_from_thread(msg)

        except Exception as e:
            # 予期しない例外 — アプリを落とさないためにすべてキャッチする
            msg = f"[AIエラー] 予期しないエラー: {e}"
            self._update_status_from_thread(msg)

    def _get_summary_filepath(self) -> str:
        """
        要約テキストの保存先パスを決定して返す。
        現在開いているファイルと同じディレクトリに ai_summary.txt を作る。
        ファイル未設定の場合はカレントディレクトリに保存する。

        Returns:
            保存先の絶対パス文字列
        """
        if self.editor.filepath:
            # 開いているファイルと同じディレクトリ
            directory = os.path.dirname(os.path.abspath(self.editor.filepath))
        else:
            # 新規バッファの場合はカレントディレクトリ
            directory = os.getcwd()
        return os.path.join(directory, "ai_summary.txt")

    def _write_and_show_summary(self, summary: str):
        """
        要約テキストをファイルに書き込み、ポップアップウィンドウで表示する。
        このメソッドはメインスレッド (root.after経由) で実行される。

        Args:
            summary: Gemma4:e2b が生成した要約テキスト
        """
        filepath = self._get_summary_filepath()

        # ファイルへ書き込む
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(summary)
            self.editor.status_message = f"[AI要約] 保存: {filepath}"
        except OSError as e:
            self.editor.status_message = f"[AIエラー] ファイル保存失敗: {e}"
            self.editor._update_status()
            return
        except Exception as e:
            self.editor.status_message = f"[AIエラー] 予期しないエラー: {e}"
            self.editor._update_status()
            return

        self.editor._update_status()

        # ポップアップウィンドウで要約を表示する
        self._show_summary_window(summary, filepath)

    def _show_summary_window(self, summary: str, filepath: str):
        """
        要約内容を表示する Toplevel ポップアップウィンドウを生成する。
        FoxEditor のカラーテーマに合わせた黒背景・オレンジ文字で表示する。

        Args:
            summary:  表示する要約テキスト
            filepath: 保存したファイルのパス (タイトルバーに表示)
        """
        win = tk.Toplevel(self.editor.root)
        win.title(f"AI要約  —  {os.path.basename(filepath)}")
        win.configure(bg=COLOR_BG)
        win.geometry("720x480")

        # タイトルラベル (ファイルパスを表示)
        title_label = tk.Label(
            win,
            text=f"保存先: {filepath}",
            bg=COLOR_STATUS_BG,
            fg=COLOR_FG,
            font=tkfont.Font(family="Consolas", size=9),
            anchor=tk.W,
            padx=8,
            pady=4,
        )
        title_label.pack(side=tk.TOP, fill=tk.X)

        # 縦スクロールバー付きテキストエリア
        frame = tk.Frame(win, bg=COLOR_BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        sb = tk.Scrollbar(frame, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        text = tk.Text(
            frame,
            bg=COLOR_BG,
            fg=COLOR_FG,
            font=tkfont.Font(family="Consolas", size=11),
            wrap=tk.WORD,   # 単語単位で折り返す
            bd=0,
            relief=tk.FLAT,
            padx=8,
            pady=8,
            yscrollcommand=sb.set,
        )
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=text.yview)

        # 要約テキストを挿入して読み取り専用にする
        text.insert("1.0", summary)
        text.config(state=tk.DISABLED)

        # 閉じるボタン
        close_btn = tk.Button(
            win,
            text="閉じる",
            command=win.destroy,
            bg=COLOR_STATUS_BG,
            fg=COLOR_FG,
            relief=tk.FLAT,
            padx=12,
            pady=4,
        )
        close_btn.pack(side=tk.BOTTOM, pady=(0, 8))

        # フォーカスをポップアップへ移す
        win.focus_set()

    def _update_status_from_thread(self, message: str):
        """
        スレッドからGUIのステータスバーを安全に更新する。
        tkinterはスレッドセーフでないため、root.after(0, ...) を使う。

        Args:
            message: ステータスバーに表示するメッセージ
        """
        def _apply():
            self.editor.status_message = message
            self.editor._update_status()

        try:
            # after(0, func) はメインスレッドのイベントループで func を実行する
            self.editor.root.after(0, _apply)
        except Exception:
            # rootが破棄済みの場合など — 無視する
            pass


# ============================================================
# LineNumberArea クラス (v0.6 新規追加)
# ============================================================
class LineNumberArea:
    """
    行番号表示エリアを担当するクラス。

    設計:
      - inner_frame の LEFT 側に Canvas を配置する
      - 初期幅は 0 (非表示); :set num で _WIDTH px に拡張
      - dlineinfo() を使って各行の y 座標を取得し、行番号テキストを描画する
      - スクロール・リサイズ・カーソル移動のたびに redraw() を呼ぶことで追従する

    スクロール同期の仕組み:
      FoxEditor._on_yscroll() がスクロールバーの set と redraw() を両方呼ぶ。
      これにより、縦スクロール発生時に行番号も自動的に再描画される。
    """

    _WIDTH = 52   # 行番号エリアのCanvas幅 (ピクセル)

    def __init__(self, editor):
        """
        Args:
            editor: FoxEditor インスタンス。inner_frame / text_area / editor_font を借用する。
        """
        self.editor   = editor
        self._visible = False

        # Canvas を inner_frame の LEFT 側に, text_area の前 (before) に挿入する
        # width=0 で初期は幅ゼロ → レイアウトに影響しない
        self.canvas = tk.Canvas(
            editor.inner_frame,
            bg=COLOR_BG,
            width=0,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.Y, before=editor.text_area)

    # ----------------------------------------------------------
    # 公開メソッド
    # ----------------------------------------------------------

    def show(self):
        """行番号エリアを有効化して初回描画する。"""
        if self._visible:
            return
        self._visible = True
        self.canvas.config(width=self._WIDTH)
        self.redraw()

    def redraw(self):
        """
        行番号を再描画する。

        処理フロー:
          1. Canvas をクリアする
          2. text_area.dlineinfo() で各行の y 座標を取得する
          3. 行番号テキストを Canvas に描画する (右寄せ)

        dlineinfo(index) は画面上に表示されている行の
        (x, y, width, height, baseline) を返す。
        表示外の行は None を返すのでループ終了の条件に使う。
        """
        if not self._visible:
            return

        self.canvas.delete("all")

        text   = self.editor.text_area
        height = text.winfo_height()
        if height <= 1:
            # ウィジェットがまだ描画されていない (起動直後など)
            return

        try:
            # 表示領域の先頭行インデックスを "@0,0" (座標指定) で取得する
            first_index = text.index("@0,0")
            row         = int(first_index.split(".")[0])

            while True:
                dline = text.dlineinfo(f"{row}.0")
                if dline is None:
                    # 行が画面外 or テキスト末尾を超えた
                    break
                _, y, _, line_h, _ = dline
                if y > height:
                    # 行の上端がウィジェット高さを超えたら描画終了
                    break

                # 行番号を Canvas 右寄せで描画する
                self.canvas.create_text(
                    self._WIDTH - 4,
                    y + line_h // 2,
                    text=str(row),
                    anchor=tk.E,
                    fill=COLOR_FG,
                    font=self.editor.editor_font,
                )
                row += 1

        except Exception:
            pass


# ============================================================
# FoxEditor メインクラス
# ============================================================
class FoxEditor:
    """
    FoxEditorのメインクラス。
    ウィンドウ生成・モード管理・キーイベント処理・ファイルIO を統括する。
    """

    def __init__(self, root: tk.Tk, filepath: str = None):
        """
        初期化処理。
        ウィジェットのセットアップ、ファイル読み込み、初期モード設定を行う。
        """
        self.root     = root
        self.filepath = filepath    # 現在開いているファイルパス (None なら新規)
        self.mode     = MODE_NORMAL # 現在のモード
        self.modified = False       # 未保存変更フラグ
        self.status_message = ""    # ステータスバーに表示する追加メッセージ

        # 行編集機能を EditCommands に委譲する
        self.edit = EditCommands(self)

        # ファイル入出力機能を FileManager に委譲する
        self.file_manager = FileManager(self)

        # v0.33 数字カウント状態管理
        self.count_buffer = ""   # 数字G などのコマンド数値入力を蓄積する文字列

        # 検索機能を SearchManager に委譲する
        self.search = SearchManager(self)

        # キー入力処理を KeyDispatcher に委譲する
        self.key_dispatcher = KeyDispatcher(self)

        # v0.5 AI機能を AIManager に委譲する
        self.ai = AIManager(self)

        # v0.6 行番号表示フラグ
        self.show_line_numbers = False

        # ウィンドウ基本設定
        self.root.title("FoxEditor")
        self.root.configure(bg=COLOR_BG)
        self.root.geometry("960x640")

        # フォント設定 (等幅フォントを優先)
        self._setup_font()

        # UIウィジェット構築 (self.inner_frame / self.sb_y / self.text_area を生成)
        self._build_ui()

        # v0.6 行番号エリア (inner_frame と text_area が必要なので _build_ui の後)
        self.line_number_area = LineNumberArea(self)

        # キーバインド設定
        self._bind_keys()

        # ファイル読み込み (引数があれば)
        if self.filepath:
            self.file_manager.load_file(self.filepath)

        # ディシジョンテーブルを構築する
        self._build_normal_key_table()
        self._build_command_table()

        # 初期モードを Normal に設定 (枠線色・編集可否を確定)
        self._set_mode(MODE_NORMAL)

        # ステータスバーの初期描画
        self._update_status()

        # テキストエリアにフォーカスを当てる
        self.text_area.focus_set()

    # ----------------------------------------------------------
    # UI構築
    # ----------------------------------------------------------

    def _setup_font(self):
        """
        エディタ用フォントを設定する。
        Windows では Consolas を優先し、なければ汎用等幅フォントを使う。
        """
        preferred = ["Consolas", "Courier New", "Courier", "monospace"]
        available = set(tkfont.families())
        chosen = "Courier New"
        for name in preferred:
            if name in available:
                chosen = name
                break
        self.editor_font = tkfont.Font(family=chosen, size=12)
        self.status_font = tkfont.Font(family=chosen, size=10)

    def _build_ui(self):
        """
        UIウィジェットを生成・配置する。

        レイアウト (上から):
          [外枠フレーム (モード色)] > [行番号Canvas(LEFT)] + [テキストエリア + スクロールバー]
          [コマンド/検索入力バー]   ← : または / 押したときだけ表示
          [ステータスバー]

        v0.6: inner_frame / sb_y をインスタンス変数化。
              yscrollcommand を _on_yscroll に変更してスクロール時の行番号再描画を実現。
        """
        # ---- 外枠フレーム ----
        # padx/pady が枠線の幅になる
        self.outer_frame = tk.Frame(
            self.root,
            bg=COLOR_BORDER_NORMAL,
            padx=3,
            pady=3,
        )
        self.outer_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        # ---- テキストエリアを入れる内枠 ----
        # v0.6: self.inner_frame としてインスタンス変数に保存
        #       LineNumberArea がここへ Canvas を追加するため
        self.inner_frame = tk.Frame(self.outer_frame, bg=COLOR_BG)
        self.inner_frame.pack(fill=tk.BOTH, expand=True)

        # 縦スクロールバー (v0.6: self.sb_y としてインスタンス変数化)
        self.sb_y = tk.Scrollbar(self.inner_frame, orient=tk.VERTICAL)
        self.sb_y.pack(side=tk.RIGHT, fill=tk.Y)

        # 横スクロールバー
        sb_x = tk.Scrollbar(self.inner_frame, orient=tk.HORIZONTAL)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)

        # テキストウィジェット本体
        # v0.6: yscrollcommand を _on_yscroll に変更
        #       → スクロール発生のたびに行番号も再描画される
        self.text_area = tk.Text(
            self.inner_frame,
            bg=COLOR_BG,
            fg=COLOR_FG,
            insertbackground=COLOR_FG,       # テキストカーソル色
            selectbackground="#cc6600",      # 選択範囲背景
            selectforeground="#ffffff",      # 選択範囲文字色
            font=self.editor_font,
            wrap=tk.NONE,                    # 折り返しなし (横スクロール対応)
            undo=True,                       # Undo を有効化
            autoseparators=True,
            yscrollcommand=self._on_yscroll,
            xscrollcommand=sb_x.set,
            bd=0,
            relief=tk.FLAT,
            padx=10,
            pady=8,
            cursor="xterm",
            spacing1=2,   # 行上部の余白
            spacing3=2,   # 行下部の余白
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)
        self.sb_y.config(command=self.text_area.yview)
        sb_x.config(command=self.text_area.xview)

        # v0.2 検索ヒット用タグを登録する
        # 一致箇所を背景:緑 / 文字:黒 で強調表示する
        self.text_area.tag_config(
            "search_hit",
            background=COLOR_SEARCH_HIT_BG,
            foreground=COLOR_SEARCH_HIT_FG,
        )

        # ---- ステータスバー (下部固定) ----
        self.status_bar = tk.Label(
            self.root,
            text="",
            bg=COLOR_STATUS_BG,
            fg=COLOR_FG,
            font=self.status_font,
            anchor=tk.W,
            padx=10,
            pady=4,
            relief=tk.FLAT,
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))

        # ---- コマンド/検索入力バー (: または / 押下時のみ表示) ----
        # ステータスバーの上に動的に pack する
        self.cmd_frame = tk.Frame(self.root, bg=COLOR_CMD_BG, bd=0)
        # 初期は非表示 (_set_mode で制御)

        # モードに応じて ":" / "/" を切り替えるプレフィックスラベル
        self.cmd_label = tk.Label(
            self.cmd_frame,
            text=":",
            bg=COLOR_CMD_BG,
            fg=COLOR_FG,
            font=self.editor_font,
        )
        self.cmd_label.pack(side=tk.LEFT, padx=(6, 0), pady=2)

        self.cmd_var = tk.StringVar()
        self.cmd_entry = tk.Entry(
            self.cmd_frame,
            textvariable=self.cmd_var,
            bg=COLOR_CMD_BG,
            fg=COLOR_FG,
            insertbackground=COLOR_FG,
            font=self.editor_font,
            bd=0,
            relief=tk.FLAT,
        )
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), pady=2)

        # Enter / Escape は _on_command_execute / _on_command_cancel で
        # 現在のモードを判定して処理を分岐する
        self.cmd_entry.bind("<Return>", self._on_command_execute)
        self.cmd_entry.bind("<Escape>", self._on_command_cancel)

    # ----------------------------------------------------------
    # キーバインド設定
    # ----------------------------------------------------------

    def _bind_keys(self):
        """
        テキストエリアのキーイベントをバインドする。
        すべてのキー入力を _on_key_press で捕捉し、モード別に振り分ける。
        """
        self.text_area.bind("<Key>", self.key_dispatcher.on_key_press)

        # マウスクリックやキー離しのタイミングでもステータスを更新
        self.text_area.bind("<ButtonRelease-1>", self._on_cursor_move)
        self.text_area.bind("<KeyRelease>", self._on_cursor_move)

        # v0.6: テキストエリアのサイズ変更時に行番号を再描画する
        self.text_area.bind("<Configure>", self._on_text_configure)

        # ウィンドウ閉じるボタン
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # モード管理
    # ----------------------------------------------------------

    def _set_mode(self, new_mode: str):
        """
        モードを切り替える。
        枠線色の更新・テキストエリアの編集可否・コマンドバーの表示切替を行う。

        Args:
            new_mode: MODE_NORMAL / MODE_INSERT / MODE_COMMAND / MODE_SEARCH のいずれか
        """
        self.mode = new_mode

        if new_mode == MODE_NORMAL:
            # テキスト直接編集を禁止し、枠線を緑に変更
            # 閲覧モードでもカーソルを出すために変更(削除)
            #self.text_area.config(state=tk.DISABLED)
            self.outer_frame.config(bg=COLOR_BORDER_NORMAL)
            # コマンド/検索バーを隠す
            self.cmd_frame.pack_forget()
            # テキストエリアへフォーカスを戻す
            self.text_area.focus_set()

        elif new_mode == MODE_INSERT:
            # テキスト編集を許可し、枠線を赤に変更
            # 閲覧モードでもカーソルを出すために変更(削除)
            #self.text_area.config(state=tk.NORMAL)
            self.outer_frame.config(bg=COLOR_BORDER_INSERT)
            # コマンド/検索バーを隠す
            self.cmd_frame.pack_forget()
            # フォーカスをテキストエリアへ戻す
            self.text_area.focus_set()

        elif new_mode == MODE_COMMAND:
            # 枠線は Normal と同じ緑に
            self.outer_frame.config(bg=COLOR_BORDER_NORMAL)
            # ラベルをコマンドモード用 ":" に設定
            self.cmd_label.config(text=":")
            # コマンド入力欄をステータスバーの直上に表示
            self.cmd_var.set("")
            self.cmd_frame.pack(
                side=tk.BOTTOM,
                fill=tk.X,
                padx=5,
                pady=(0, 2),
                before=self.status_bar,
            )
            self.cmd_entry.focus_set()

        elif new_mode == MODE_SEARCH:
            # v0.2 検索モード: 枠線は Normal と同じ緑
            self.outer_frame.config(bg=COLOR_BORDER_NORMAL)
            # ラベルを検索モード用 "/" に設定
            self.cmd_label.config(text="/")
            # 検索入力欄をステータスバーの直上に表示
            self.cmd_var.set("")
            self.cmd_frame.pack(
                side=tk.BOTTOM,
                fill=tk.X,
                padx=5,
                pady=(0, 2),
                before=self.status_bar,
            )
            self.cmd_entry.focus_set()

        self._update_status()

    def _move_cursor_vertical(self, delta: int):
        """
        カーソルを垂直方向に delta 行だけ移動する。
        行境界を超えないようクランプし、移動先の列もクランプする。

        Args:
            delta: 正の値で下方向、負の値で上方向
        """
        pos = self.text_area.index(tk.INSERT)
        row, col = map(int, pos.split("."))

        new_row = max(1, row + delta)
        # テキスト末尾の行番号を取得 (末尾は "N.0" 形式)
        last_row = int(self.text_area.index(tk.END).split(".")[0]) - 1
        new_row = min(new_row, max(last_row, 1))

        # 移動先行の末尾カラムを超えないようにクランプ
        line_end_col = int(self.text_area.index(f"{new_row}.end").split(".")[1])
        new_col = min(col, line_end_col)

        self.text_area.mark_set(tk.INSERT, f"{new_row}.{new_col}")
        self.text_area.see(tk.INSERT)
        self._update_status()

    def _delete_char_at_cursor(self):
        """
        Normal モードの 'x' コマンド: カーソル位置の1文字を削除する。
        行末改行文字は削除しない。
        """
        # 一時的に編集可能にして削除し、再び禁止する
        #閲覧モードカーソル表示のため削除
        #self.text_area.config(state=tk.NORMAL)
        cursor = self.text_area.index(tk.INSERT)
        line_end = self.text_area.index(f"{cursor} lineend")
        if cursor != line_end:
            self.text_area.delete(cursor)
            self.modified = True
        #閲覧モードカーソル表示のため削除
        #self.text_area.config(state=tk.DISABLED)
        self._update_status()

    def _on_cursor_move(self, *_):
        """
        カーソル移動後にステータスバーと行番号を更新する。
        v0.6: show_line_numbers が True なら行番号も再描画する。
        """
        # Insert モードで文字が入力されたらフラグを立てる
        if self.mode == MODE_INSERT:
            self.modified = True
        if self.show_line_numbers:
            self.line_number_area.redraw()
        self._update_status()

    def _on_text_configure(self, *_):
        """
        テキストエリアのリサイズ時に行番号を再描画する。
        ウィンドウのリサイズで行の y 座標が変わるため必要。
        """
        if self.show_line_numbers:
            self.line_number_area.redraw()

    def _move_left(self):
        """h キー: カーソルを左へ1文字移動する。"""
        self.text_area.mark_set(tk.INSERT, f"{tk.INSERT} -1c")
        self._update_status()

    def _move_right(self):
        """l キー: カーソルを右へ1文字移動する。"""
        self.text_area.mark_set(tk.INSERT, f"{tk.INSERT} +1c")
        self._update_status()

    # ----------------------------------------------------------
    # v0.6 新規移動・編集コマンド
    # ----------------------------------------------------------

    def _move_line_start(self):
        """0 キー: 現在行の先頭へカーソルを移動する。"""
        row = self.text_area.index(tk.INSERT).split(".")[0]
        self.text_area.mark_set(tk.INSERT, f"{row}.0")
        self.text_area.see(tk.INSERT)
        self.status_message = ""
        self._update_status()

    def _move_line_end(self):
        """$ キー: 現在行の末尾へカーソルを移動する。"""
        row = self.text_area.index(tk.INSERT).split(".")[0]
        self.text_area.mark_set(tk.INSERT, f"{row}.end")
        self.text_area.see(tk.INSERT)
        self.status_message = ""
        self._update_status()

    def _open_line_below(self):
        """
        o キー: 現在行の下に新規行を挿入し、Insertモードへ移行する。
        viの o コマンドと同等の動作。
        """
        row      = self.text_area.index(tk.INSERT).split(".")[0]
        line_end = self.text_area.index(f"{row}.end")
        self.text_area.insert(line_end, "\n")
        self.text_area.mark_set(tk.INSERT, f"{int(row) + 1}.0")
        self.text_area.see(tk.INSERT)
        self.modified = True
        self.status_message = ""
        self._set_mode(MODE_INSERT)

    def _undo(self):
        """
        u キー: 直前の操作をUndoする。
        tkinter Text の edit_undo() を利用。エラーは無視してアプリを落とさない。
        """
        try:
            self.text_area.edit_undo()
            self.modified = True
            self.status_message = "Undoしました"
        except Exception:
            self.status_message = "Undoできません"
        if self.show_line_numbers:
            self.line_number_area.redraw()
        self._update_status()

    # ----------------------------------------------------------
    # v0.6 行番号表示
    # ----------------------------------------------------------

    def _enable_line_numbers(self):
        """
        :set num コマンド: 行番号表示をONにする。
        LineNumberArea.show() を呼び出してCanvas幅を拡張・初回描画する。
        """
        self.show_line_numbers = True
        self.line_number_area.show()
        self.status_message = "行番号表示 ON"
        self._update_status()

    def _on_yscroll(self, *args):
        """
        テキストエリアの縦スクロールコールバック。
        スクロールバーを更新した後、行番号も再描画する。

        tkinter Text の yscrollcommand に登録する。
        スクロール発生のたびに呼ばれ、行番号の追従を実現する。
        """
        self.sb_y.set(*args)
        if self.show_line_numbers:
            self.line_number_area.redraw()

    # ----------------------------------------------------------
    # ディシジョンテーブル (C言語の関数ポインタテーブル相当)
    # ----------------------------------------------------------

    def _build_normal_key_table(self):
        """
        Normal モードのキーと処理関数を対応付けるテーブルを構築する。
        dd/yy (pending_key), Ctrl+D/U, 数字, G は従来の専用処理を維持する。
        v0.6: $, o, u を追加。0 は KeyDispatcher 側で特殊処理するためここには含めない。
        """
        self.normal_key_table = {
            "h": self._move_left,
            "j": lambda: self._move_cursor_vertical(+1),
            "k": lambda: self._move_cursor_vertical(-1),
            "l": self._move_right,
            "i": lambda: self._set_mode(MODE_INSERT),
            "x": self._delete_char_at_cursor,
            ":": lambda: self._set_mode(MODE_COMMAND),
            "/": self.search.start,
            "n": self.search.next,
            "N": self.search.prev,
            "p": self.edit.paste_line_below,
            # v0.6 新規追加
            "$": self._move_line_end,
            "o": self._open_line_below,
            "u": self._undo,
        }

    def _build_command_table(self):
        """
        Command モードのコマンド文字列と処理関数を対応付けるテーブルを構築する。
        v0.5: gemma4_smr を追加。
        v0.6: set num を追加。
        """
        self.command_table = {
            "w":          self.file_manager.save_file,
            "q":          self._quit,
            "q!":         self._quit_force,
            "wq":         self._save_and_quit,
            # v0.5 AIコード要約コマンド
            "gemma4_smr": self.ai.summarize_code,
            # v0.6 行番号表示コマンド
            "set num":    self._enable_line_numbers,
        }

    def _save_and_quit(self):
        """
        :wq コマンド: 保存に成功した場合のみウィンドウを閉じる。
        """
        if self.file_manager.save_file():
            self.root.destroy()
        else:
            self._set_mode(MODE_NORMAL)

    # ----------------------------------------------------------
    # コマンド入力処理
    # ----------------------------------------------------------

    def _on_command_execute(self, *_):
        """
        コマンド/検索入力欄で Enter が押されたときに実行する。
        現在のモードを判定してコマンド実行または検索実行へ振り分ける。
        """
        if self.mode == MODE_SEARCH:
            # 検索実行を SearchManager に委譲する
            self.search.execute()
        else:
            # COMMAND モードならコマンドを実行する
            self._execute_command()

    def _execute_command(self):
        """
        COMMAND モードの入力テキストを解釈して実行する。
        command_table で処理関数を引き、未知のコマンドはエラー表示する。
        v0.5: gemma4_smr はNormalモードへ戻してからAI処理を開始する。
        """
        cmd = self.cmd_var.get().strip()
        func = self.command_table.get(cmd)

        if func:
            # AIコマンドは先にNormalモードへ戻す (コマンドバーを閉じる)
            # スレッド実行中にコマンドバーが開いたままになるのを防ぐ
            if cmd == "gemma4_smr":
                self._set_mode(MODE_NORMAL)
                func()
            elif cmd == "w":
                func()
                self._set_mode(MODE_NORMAL)
            elif cmd == "wq":
                func()
                # _save_and_quit 内で終了またはモード切替するので何もしない
            elif cmd in ("q", "q!"):
                func()
                # 各関数内で終了処理するので何もしない
            else:
                func()
                self._set_mode(MODE_NORMAL)
        else:
            self.status_message = f"不明なコマンド: :{cmd}"
            self._set_mode(MODE_NORMAL)

    def _on_command_cancel(self, *_):
        """
        コマンド/検索入力中に Esc を押したらキャンセルして Normal モードへ戻る。
        検索ハイライトは維持する (n/N で再利用できるようにするため)。
        """
        self.status_message = ""
        self._set_mode(MODE_NORMAL)
        self.text_area.focus_set()

    # ----------------------------------------------------------
    # v0.33 スクロール・行ジャンプ
    # ----------------------------------------------------------

    def _scroll_half_page(self, delta: int):
        """
        Ctrl+D / Ctrl+U: カーソルを delta 行移動し画面内へスクロールする。
        delta が正なら下方向、負なら上方向。列位置は既存ロジックに従いクランプする。

        Args:
            delta: 移動行数 (+15 で半ページ下、-15 で半ページ上)
        """
        self._move_cursor_vertical(delta)

    def _go_to_line(self, line_no: int):
        """
        指定行番号へカーソルを移動する。
        範囲外はクランプして安全に処理する。

        Args:
            line_no: ジャンプ先の行番号 (1 始まり)
        """
        last_row = int(self.text_area.index(tk.END).split(".")[0]) - 1
        line_no = max(1, min(line_no, max(last_row, 1)))
        self.text_area.mark_set(tk.INSERT, f"{line_no}.0")
        self.text_area.see(tk.INSERT)
        self._update_status()

    def _go_to_last_line(self):
        """最終行へカーソルを移動する。"""
        last_row = int(self.text_area.index(tk.END).split(".")[0]) - 1
        self._go_to_line(max(last_row, 1))

    def _clear_count_buffer(self):
        """count_buffer をリセットする。"""
        self.count_buffer = ""

    # ----------------------------------------------------------
    # 終了処理
    # ----------------------------------------------------------

    def _quit(self):
        """
        :q コマンドによる終了処理。
        未保存変更がある場合はステータスバーに警告を表示する。
        もう一度 :q を実行すると強制終了する (Vim 風の挙動)。
        """
        if self.modified:
            if "[警告]" in self.status_message:
                # 2回目の :q → 強制終了
                self.root.destroy()
            else:
                # 1回目の :q → 警告だけ出す (コマンドバーを閉じてから警告を表示する)
                self.status_message = "[警告] 未保存の変更あり。:q でもう一度実行すると強制終了 / :w で保存"
                self._set_mode(MODE_NORMAL)
        else:
            self.root.destroy()

    def _quit_force(self):
        """
        :q! コマンドによる強制終了
        """
        # 強制終了
        self.root.destroy()

    def _on_close(self):
        """
        ウィンドウの × ボタンが押されたときの処理。
        未保存変更がある場合はダイアログで保存を確認する。
        """
        if self.modified:
            from tkinter import messagebox
            answer = messagebox.askyesnocancel(
                "FoxEditor - 終了確認",
                "未保存の変更があります。\n保存して終了しますか？",
            )
            if answer is True:
                if self.file_manager.save_file():
                    self.root.destroy()
                # 保存失敗時はウィンドウを閉じない
            elif answer is False:
                # 保存せず終了
                self.root.destroy()
            # Cancel (None) の場合は何もしない
        else:
            self.root.destroy()

    # ----------------------------------------------------------
    # ステータスバー更新
    # ----------------------------------------------------------

    def _update_status(self):
        """
        ステータスバーの表示を更新する。
        モード・ファイル名・行番号・列番号・追加メッセージを表示する。
        """
        # モード表示文字列
        mode_label = {
            MODE_NORMAL:  "-- NORMAL --",
            MODE_INSERT:  "-- INSERT --",
            MODE_COMMAND: "-- COMMAND --",
            MODE_SEARCH:  "-- SEARCH --",   # v0.2
        }.get(self.mode, self.mode)

        # ファイル名 (未設定なら [新規バッファ]、変更ありなら * を付ける)
        if self.filepath:
            filename = os.path.basename(self.filepath)
        else:
            filename = "[新規バッファ]"
        if self.modified:
            filename += " *"

        # カーソル位置 (行・列)
        try:
            pos_str = self.text_area.index(tk.INSERT)
            row, col = map(int, pos_str.split("."))
            position = f"行:{row}  列:{col + 1}"
        except Exception:
            position = "行:1  列:1"

        # ステータス文字列を組み立てる
        # 例: "-- NORMAL --  |  memo.txt *  |  行:3  列:12  |  [42]  |  保存しました"
        segments = [mode_label, filename, position]
        if self.count_buffer:
            segments.append(f"[{self.count_buffer}]")
        if self.status_message:
            segments.append(self.status_message)

        status_text = "  |  ".join(segments)
        self.status_bar.config(text=f"  {status_text}")


# ============================================================
# エントリーポイント
# ============================================================

def main():
    """
    FoxEditor を起動するエントリーポイント。
    コマンドライン引数からファイルパスを取得し、tkinter ウィンドウを起動する。

    使い方:
        python foxeditor.py           → 空バッファで起動
        python foxeditor.py memo.txt  → memo.txt を開いて起動

    フロー：
        tk.Tk()でウィンドウを作る
        ↓
        FoxEditor(root, filepath)でFoxEditor本体を作る
        ↓
        __init__()が自動で呼ばれる
        ↓
        画面・フォント・キー操作・ファイル読み込みを準備する
        ↓
        mainloop()でアプリ開始
    """
    filepath = sys.argv[1] if len(sys.argv) >= 2 else None

    root = tk.Tk()
    FoxEditor(root, filepath=filepath)
    root.mainloop()


if __name__ == "__main__":
    main()
