"""Manual Microsoft Dynamics NAV inventory adjustment work queue."""
import csv
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import theme
from .widgets import SpinnerLabel
from ..services.api_client import ApiClient, ApiError, SessionExpired


class NavAdjustmentsView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.rows = []
        self._build()
        self.refresh()

    def _build(self):
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        top = ttk.Frame(self); top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12,6))
        ttk.Label(top, text="NAV ADJUSTMENTS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel"); self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel"); self.spinner.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Button(top, text="Export CSV…", command=self._export).pack(side="right", padx=6)
        self.edit_btn = ttk.Button(top, text="Update…", command=self._edit, state="disabled")
        self.edit_btn.pack(side="right", padx=6)
        self.status_var = tk.StringVar(value="pending")
        status = ttk.Combobox(top, textvariable=self.status_var, values=["pending","posted","all"], width=10, state="readonly")
        status.pack(side="right", padx=6); status.bind("<<ComboboxSelected>>", lambda e:self.refresh())

        wrap=ttk.Frame(self); wrap.grid(row=1,column=0,sticky="nsew",padx=12,pady=(0,12)); wrap.columnconfigure(0,weight=1); wrap.rowconfigure(0,weight=1)
        cols=("order","project","code","item","nav","qty","completed","status","notes")
        self.tree=ttk.Treeview(wrap,columns=cols,show="headings",selectmode="browse")
        heads={"order":("Order",70),"project":("Project / Event",180),"code":("App Part #",105),"item":("Item",180),"nav":("NAV Item #",110),"qty":("Qty",55),"completed":("Completed",130),"status":("Status",70),"notes":("Notes",220)}
        for c,(label,width) in heads.items():
            self.tree.heading(c,text=label); self.tree.column(c,width=width,anchor="e" if c=="qty" else "w")
        self.tree.grid(row=0,column=0,sticky="nsew")
        sb=ttk.Scrollbar(wrap,orient="vertical",command=self.tree.yview); sb.grid(row=0,column=1,sticky="ns"); self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("posted", foreground=theme.OK)
        self.tree.bind("<<TreeviewSelect>>", lambda e:self.edit_btn.configure(state="normal" if self.tree.selection() else "disabled"))
        self.tree.bind("<Double-1>", lambda e:self._edit())

    def refresh(self):
        self.spinner.start("Loading NAV queue")
        def work():
            try: rows=self.api.nav_adjustments(self.status_var.get())
            except SessionExpired: return
            except ApiError as exc:
                self.after(0, lambda:(self.spinner.stop(), theme.show_error(self,"Load failed",str(exc)))); return
            self.after(0, lambda:self._set_rows(rows))
        threading.Thread(target=work,daemon=True).start()

    def _set_rows(self, rows):
        if not self.winfo_exists(): return
        self.spinner.stop(); self.rows=rows; self.tree.delete(*self.tree.get_children())
        for row in rows:
            dt=str(row.get("fulfilled_at") or "").replace("T"," ")[:16]
            self.tree.insert("","end",iid=str(row["id"]),values=(f"#{row['order_id']}",row.get("project") or "",row.get("item_code") or "",row.get("item_name") or "",row.get("nav_item_number") or "",row.get("quantity_shipped"),dt,row.get("status","").title(),row.get("notes") or ""),tags=("posted",) if row.get("status")=="posted" else ())
        pending=sum(1 for r in rows if r.get("status")=="pending")
        self.count_lbl.configure(text=f"{len(rows)} entries" + (f" · {pending} pending" if pending else ""))
        self.edit_btn.configure(state="disabled")

    def _selected(self):
        sel=self.tree.selection();
        if not sel: return None
        return next((r for r in self.rows if r["id"]==int(sel[0])),None)

    def _edit(self):
        row=self._selected()
        if not row: return
        win=tk.Toplevel(self); win.title(f"NAV adjustment · Order #{row['order_id']}"); win.configure(bg=theme.PAPER); win.transient(self.winfo_toplevel()); win.grab_set()
        frm=ttk.Frame(win,padding=16); frm.pack(fill="both",expand=True)
        ttk.Label(frm,text=f"{row['item_code']} · {row['item_name']}",font=theme.FONT_BOLD).pack(anchor="w")
        ttk.Label(frm,text=f"NAV item: {row.get('nav_item_number') or 'Not supplied'}   Quantity shipped: {row['quantity_shipped']}",style="Muted.TLabel").pack(anchor="w",pady=(3,12))
        status=tk.StringVar(value=row.get("status","pending")); notes=tk.StringVar(value=row.get("notes", ""))
        ttk.Label(frm,text="Status").pack(anchor="w"); ttk.Combobox(frm,textvariable=status,values=["pending","posted"],state="readonly").pack(fill="x",pady=(3,8))
        ttk.Label(frm,text="Notes").pack(anchor="w"); ttk.Entry(frm,textvariable=notes,width=55).pack(fill="x",pady=(3,12))
        buttons=ttk.Frame(frm); buttons.pack(fill="x")
        def save():
            def work():
                try:self.api.update_nav_adjustment(row["id"],status.get(),notes.get().strip())
                except SessionExpired:return
                except ApiError as exc:self.after(0,lambda:theme.show_error(win,"Update failed",str(exc)));return
                self.after(0,lambda:(win.destroy(),self.refresh()))
            threading.Thread(target=work,daemon=True).start()
        ttk.Button(buttons,text="Cancel",command=win.destroy).pack(side="right",padx=(8,0)); ttk.Button(buttons,text="Save",style="Primary.TButton",command=save).pack(side="right")

    def _export(self):
        if not self.rows:
            messagebox.showinfo("Export NAV adjustments","There are no rows to export.",parent=self); return
        path=filedialog.asksaveasfilename(parent=self,title="Export NAV adjustments",defaultextension=".csv",filetypes=[("CSV files","*.csv")])
        if not path:return
        fields=["order_id","project","item_code","item_name","nav_item_number","quantity_shipped","fulfilled_at","status","notes","posted_at","posted_by"]
        try:
            with open(path,"w",newline="",encoding="utf-8-sig") as fh:
                w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows({k:r.get(k) for k in fields} for r in self.rows)
        except OSError as exc:
            theme.show_error(self,"Export failed",str(exc)); return
        messagebox.showinfo("Export complete",f"Saved {len(self.rows)} NAV adjustment rows.",parent=self)
