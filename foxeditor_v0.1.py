"""
FoxEditor - viライクなGUIテキストエディタ
Windows上で動作するPython/tkinter製の軽量エディタ

起動方法:
    python foxeditor.py           # 空バッファで起動
    python foxeditor.py memo.txt  # ファイルを開いて起動
"""

import tkinter as tk
from tkinter import font as tkfont
import sys
import os

# ============================================================
# カラーテーマ定数
# ============================================================
COLOR_BG            = "#000000"   # 背景色: 黒
COLOR_FG            = "#ff9900"   # テキスト文字色: オレンジ
COLOR_BORDER_NORMAL = "#00ff66"   # Normalモード枠線: 緑
COLOR_BORDER_INSERT = "#ff3030"   # Insertモード枠線: 赤
COLOR_STATUS_BG     = "#111111"   # ステータスバー背景: 濃いグレー
COLOR_CMD_BG        = "#0a0a0a"   # コマンド入力欄背景

# ============================================================
# モード定数
# ============================================================
MODE_NORMAL  = "NORMAL"
MODE_INSERT  = "INSERT"
MODE_COMMAND = "COMMAND"


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

        # ウィンドウ基本設定
        self.root.title("FoxEditor")
        self.root.configure(bg=COLOR_BG)
        self.root.geometry("960x640")

        # フォント設定 (等幅フォントを優先)
        self._setup_font()

        # UIウィジェット構築
        self._build_ui()

        # キーバインド設定
        self._bind_keys()

        # ファイル読み込み (引数があれば)
        if self.filepath:
            self._load_file(self.filepath)

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
          [外枠フレーム (モード色)] > [テキストエリア + スクロールバー]
          [コマンド入力バー]   ← :押したときだけ表示
          [ステータスバー]
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
        inner_frame = tk.Frame(self.outer_frame, bg=COLOR_BG)
        inner_frame.pack(fill=tk.BOTH, expand=True)

        # 縦スクロールバー
        sb_y = tk.Scrollbar(inner_frame, orient=tk.VERTICAL)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)

        # 横スクロールバー
        sb_x = tk.Scrollbar(inner_frame, orient=tk.HORIZONTAL)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)

        # テキストウィジェット本体
        self.text_area = tk.Text(
            inner_frame,
            bg=COLOR_BG,
            fg=COLOR_FG,
            insertbackground=COLOR_FG,       # テキストカーソル色
            selectbackground="#cc6600",      # 選択範囲背景
            selectforeground="#ffffff",      # 選択範囲文字色
            font=self.editor_font,
            wrap=tk.NONE,                    # 折り返しなし (横スクロール対応)
            undo=True,                       # Undo を有効化
            autoseparators=True,
            yscrollcommand=sb_y.set,
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
        sb_y.config(command=self.text_area.yview)
        sb_x.config(command=self.text_area.xview)

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

        # ---- コマンド入力バー (: 押下時のみ表示) ----
        # ステータスバーの上に動的に pack する
        self.cmd_frame = tk.Frame(self.root, bg=COLOR_CMD_BG, bd=0)
        # 初期は非表示 (_set_mode で制御)

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

        # コマンド確定 / キャンセル
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
        self.text_area.bind("<Key>", self._on_key_press)

        # マウスクリックやキー離しのタイミングでもステータスを更新
        self.text_area.bind("<ButtonRelease-1>", self._on_cursor_move)
        self.text_area.bind("<KeyRelease>", self._on_cursor_move)

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
            new_mode: MODE_NORMAL / MODE_INSERT / MODE_COMMAND のいずれか
        """
        self.mode = new_mode

        if new_mode == MODE_NORMAL:
            # テキスト直接編集を禁止し、枠線を緑に変更
            self.text_area.config(state=tk.DISABLED)
            self.outer_frame.config(bg=COLOR_BORDER_NORMAL)
            # コマンドバーを隠す
            self.cmd_frame.pack_forget()

        elif new_mode == MODE_INSERT:
            # テキスト編集を許可し、枠線を赤に変更
            self.text_area.config(state=tk.NORMAL)
            self.outer_frame.config(bg=COLOR_BORDER_INSERT)
            # コマンドバーを隠す
            self.cmd_frame.pack_forget()
            # フォーカスをテキストエリアへ戻す
            self.text_area.focus_set()

        elif new_mode == MODE_COMMAND:
            # 枠線は Normal と同じ緑に
            self.outer_frame.config(bg=COLOR_BORDER_NORMAL)
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

        self._update_status()

    # ----------------------------------------------------------
    # キーイベント処理
    # ----------------------------------------------------------

    def _on_key_press(self, event: tk.Event):
        """
        テキストエリアへのキー入力を処理するメインハンドラ。
        現在のモードに応じて Normal / Insert の処理メソッドへ委譲する。

        Returns:
            "break" を返すと tkinter のデフォルト処理をキャンセルする。
        """
        if self.mode == MODE_NORMAL:
            return self._handle_normal_key(event)
        elif self.mode == MODE_INSERT:
            return self._handle_insert_key(event)
        # COMMAND モードは cmd_entry が別ウィジェットなのでここには来ない
        return None

    def _handle_normal_key(self, event: tk.Event):
        """
        Normalモードのキー操作を処理する。

        対応キー:
          h/j/k/l : カーソル移動 (vi 方向キー)
          i       : Insert モードへ移行
          x       : カーソル位置の1文字削除
          :       : コマンド入力モードへ
        """
        char = event.char    # 入力された文字 ("h", ":", など)

        if char == "h":
            # 左へ1文字移動
            self.text_area.mark_set(tk.INSERT, f"{tk.INSERT} -1c")
            self._update_status()
            return "break"

        elif char == "j":
            # 下へ1行移動
            self._move_cursor_vertical(+1)
            return "break"

        elif char == "k":
            # 上へ1行移動
            self._move_cursor_vertical(-1)
            return "break"

        elif char == "l":
            # 右へ1文字移動
            self.text_area.mark_set(tk.INSERT, f"{tk.INSERT} +1c")
            self._update_status()
            return "break"

        elif char == "i":
            # Insert モードへ移行
            self.status_message = ""
            self._set_mode(MODE_INSERT)
            return "break"

        elif char == "x":
            # カーソル位置の1文字を削除
            self._delete_char_at_cursor()
            return "break"

        elif char == ":":
            # コマンド入力モードへ
            self._set_mode(MODE_COMMAND)
            return "break"

        # それ以外のキーはすべて無視 (Normal モードは編集不可)
        return "break"

    def _handle_insert_key(self, event: tk.Event):
        """
        Insertモードのキー操作を処理する。

        Esc のみ特別処理し、それ以外は tkinter のデフォルト処理に委ねる
        (文字入力・Backspace・Enter はデフォルト動作で動く)。
        """
        if event.keysym == "Escape":
            # Esc で Normal モードへ戻る
            self.status_message = ""
            self.modified = True
            self._set_mode(MODE_NORMAL)
            return "break"

        # その他はデフォルト処理 (文字挿入・Backspace・Enter 等)
        # modified フラグは後続の KeyRelease で更新される
        return None

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
        self.text_area.config(state=tk.NORMAL)
        cursor = self.text_area.index(tk.INSERT)
        line_end = self.text_area.index(f"{cursor} lineend")
        if cursor != line_end:
            self.text_area.delete(cursor)
            self.modified = True
        self.text_area.config(state=tk.DISABLED)
        self._update_status()

    def _on_cursor_move(self, *_):
        """カーソル移動後にステータスバーを更新する。"""
        # Insert モードで文字が入力されたらフラグを立てる
        if self.mode == MODE_INSERT:
            self.modified = True
        self._update_status()

    # ----------------------------------------------------------
    # コマンド入力処理
    # ----------------------------------------------------------

    def _on_command_execute(self, *_):
        """
        コマンド入力欄で Enter が押されたときに実行する。
        :w / :q / :wq を処理し、未知のコマンドはエラー表示する。
        """
        cmd = self.cmd_var.get().strip()

        if cmd == "w":
            # 保存
            self._save_file()
            self._set_mode(MODE_NORMAL)

        elif cmd == "q":
            # 終了 (未保存確認あり)
            self._set_mode(MODE_NORMAL)
            self._quit()

        elif cmd == "wq":
            # 保存して終了
            if self._save_file():
                self.root.destroy()
            else:
                self._set_mode(MODE_NORMAL)

        else:
            self.status_message = f"不明なコマンド: :{cmd}"
            self._set_mode(MODE_NORMAL)

    def _on_command_cancel(self, *_):
        """
        コマンド入力中に Esc を押したらキャンセルして Normal モードへ戻る。
        """
        self.status_message = ""
        self._set_mode(MODE_NORMAL)
        self.text_area.focus_set()

    # ----------------------------------------------------------
    # ファイル読み込み処理
    # ----------------------------------------------------------

    def _load_file(self, filepath: str):
        """
        指定されたファイルを読み込んでテキストエリアに表示する。
        ファイルが存在しない場合は新規ファイルとして扱い、エラーにしない。
        読み込み失敗時はステータスバーにエラー内容を表示し、アプリは落とさない。

        Args:
            filepath: 読み込むファイルのパス
        """
        if not os.path.exists(filepath):
            # 存在しないファイル → 新規作成扱い
            self.status_message = f"新規ファイル: {os.path.basename(filepath)}"
            return

        try:
            # まず UTF-8 で試みる
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self._insert_content(content)
            self.status_message = f"読み込み完了: {os.path.basename(filepath)}"

        except UnicodeDecodeError:
            # UTF-8 で失敗したら CP932 (Shift-JIS) で再試行
            try:
                with open(filepath, "r", encoding="cp932") as f:
                    content = f.read()
                self._insert_content(content)
                self.status_message = f"読み込み完了 (CP932): {os.path.basename(filepath)}"
            except Exception as e:
                self.status_message = f"[エラー] ファイル読み込み失敗: {e}"

        except OSError as e:
            self.status_message = f"[エラー] ファイルを開けません: {e}"
        except Exception as e:
            self.status_message = f"[エラー] 予期しないエラー: {e}"

    def _insert_content(self, content: str):
        """
        テキストエリアにコンテンツを挿入する共通処理。
        一時的に編集可能にしてコンテンツをセットし、カーソルを先頭に戻す。
        """
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0", content)
        self.text_area.mark_set(tk.INSERT, "1.0")
        self.text_area.see(tk.INSERT)
        self.modified = False

    # ----------------------------------------------------------
    # 保存処理
    # ----------------------------------------------------------

    def _save_file(self) -> bool:
        """
        現在のテキストエリアの内容をファイルに保存する。
        失敗時はステータスバーにエラー内容を表示し、アプリは落とさない。

        Returns:
            True: 保存成功 / False: 保存失敗またはファイルパス未設定
        """
        if not self.filepath:
            # ファイルパス未設定 (引数なし起動の新規バッファ)
            self.status_message = "[エラー] ファイル名が未設定です。引数付きで起動してください"
            return False

        try:
            # テキスト全体を取得
            # tkinter の Text ウィジェットは末尾に自動で改行を付けるので除去する
            content = self.text_area.get("1.0", tk.END)
            if content.endswith("\n"):
                content = content[:-1]

            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.modified = False
            self.status_message = f"保存しました: {os.path.basename(self.filepath)}"
            return True

        except OSError as e:
            self.status_message = f"[エラー] 保存失敗 (OS エラー): {e}"
            return False
        except Exception as e:
            self.status_message = f"[エラー] 保存中に予期しないエラー: {e}"
            return False

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
                # 1回目の :q → 警告だけ出す
                self.status_message = "[警告] 未保存の変更あり。:q でもう一度実行すると強制終了 / :w で保存"
                self._update_status()
        else:
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
                if self._save_file():
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
        # 例: "-- NORMAL --  |  memo.txt *  |  行:3  列:12  |  保存しました"
        segments = [mode_label, filename, position]
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
    """
    filepath = sys.argv[1] if len(sys.argv) >= 2 else None

    root = tk.Tk()
    FoxEditor(root, filepath=filepath)
    root.mainloop()


if __name__ == "__main__":
    main()
