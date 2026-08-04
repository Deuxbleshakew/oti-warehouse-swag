import threading, tkinter as tk
from tkinter import ttk, messagebox
from . import theme
from ..services.api_client import ApiError
class KitsView(ttk.Frame):
 def __init__(self,parent,api):
  super().__init__(parent);self.api=api;self.kits=[];self.items=[];self._build();self.refresh()
 def _build(self):
  top=ttk.Frame(self);top.pack(fill="x",padx=12,pady=12);ttk.Label(top,text="KITS & SWAG SETS",style="Head.TLabel").pack(side="left")
  ttk.Button(top,text="Refresh",command=self.refresh).pack(side="right");ttk.Button(top,text="Delete",style="Danger.TButton",command=self.delete).pack(side="right",padx=6);ttk.Button(top,text="New Kit…",style="Primary.TButton",command=lambda:self.dialog()).pack(side="right")
  self.tree=ttk.Treeview(self,columns=("code","name","buildable","components","active"),show="headings")
  for c,t,w in [("code","Code",120),("name","Kit name",260),("buildable","Buildable",90),("components","Components",100),("active","Active",70)]:self.tree.heading(c,text=t);self.tree.column(c,width=w)
  self.tree.pack(fill="both",expand=True,padx=12,pady=(0,12));self.tree.bind("<Double-1>",lambda e:self.dialog(self.selected()))
 def selected(self):
  sel=self.tree.selection();return next((k for k in self.kits if str(k["id"])==sel[0]),None) if sel else None
 def refresh(self):
  def work():
   try:k=self.api.list_kits();i=self.api.all_items()
   except ApiError as e:self.after(0,lambda:theme.show_error(self,"Load failed",str(e)));return
   self.after(0,lambda:self.setdata(k,i))
  threading.Thread(target=work,daemon=True).start()
 def setdata(self,kits,items):
  self.kits,self.items=kits,items;self.tree.delete(*self.tree.get_children())
  for k in kits:self.tree.insert("","end",iid=str(k["id"]),values=(k["code"],k["name"],k["buildable_quantity"],len(k["components"]),"yes" if k["active"] else "no"))
 def dialog(self,kit=None):
  w=tk.Toplevel(self);w.title("Edit Kit" if kit else "New Kit");w.transient(self.winfo_toplevel());w.grab_set();f=ttk.Frame(w,padding=16);f.pack(fill="both",expand=True)
  nv=tk.StringVar(value=kit["name"] if kit else "");cv=tk.StringVar(value=kit["code"] if kit else "KIT-");av=tk.BooleanVar(value=kit["active"] if kit else True)
  for r,(lab,var) in enumerate((("Kit name",nv),("Kit code",cv))):ttk.Label(f,text=lab).grid(row=r,column=0,sticky="w",pady=4);ttk.Entry(f,textvariable=var,width=34).grid(row=r,column=1,sticky="ew")
  ttk.Checkbutton(f,text="Active",variable=av).grid(row=2,column=1,sticky="w")
  ttk.Label(f,text="Components (select item, quantity, then Add)").grid(row=3,column=0,columnspan=2,sticky="w",pady=(10,3))
  choices=[f'{i["code"]} — {i["name"]}' for i in self.items if i.get("active")]; itemmap={f'{i["code"]} — {i["name"]}':i for i in self.items}
  pick=tk.StringVar();qty=tk.IntVar(value=1);row=ttk.Frame(f);row.grid(row=4,column=0,columnspan=2,sticky="ew");ttk.Combobox(row,textvariable=pick,values=choices,width=42,state="readonly").pack(side="left");ttk.Spinbox(row,from_=1,to=999,textvariable=qty,width=6).pack(side="left",padx=5)
  lb=tk.Listbox(f,width=62,height=9);lb.grid(row=5,column=0,columnspan=2,sticky="nsew",pady=6); comps=[]
  def redraw():lb.delete(0,"end");[lb.insert("end",f'{n+1}. {c["quantity"]} × {c["item_code"]} — {c["item_name"]}') for n,c in enumerate(comps)]
  if kit:comps.extend(kit["components"]);redraw()
  def add():
   it=itemmap.get(pick.get());
   if it:comps.append({"item_id":it["id"],"item_code":it["code"],"item_name":it["name"],"quantity":qty.get(),"position":len(comps)});redraw()
  ttk.Button(row,text="Add",command=add).pack(side="left")
  def remove():
   if lb.curselection():comps.pop(lb.curselection()[0]);redraw()
  ttk.Button(f,text="Remove selected",command=remove).grid(row=6,column=0,sticky="w")
  def save():
   if not nv.get().strip() or not cv.get().strip() or not comps:return theme.show_error(w,"Missing information","Name, code, and at least one component are required.")
   data={"name":nv.get().strip(),"code":cv.get().strip(),"description":"","active":av.get(),"custom":False,"saved_for_reuse":True,"components":[{"item_id":c["item_id"],"quantity":int(c["quantity"]),"position":n} for n,c in enumerate(comps)]}
   try:self.api.update_kit(kit["id"],data) if kit else self.api.create_kit(data)
   except ApiError as e:return theme.show_error(w,"Save failed",str(e))
   w.destroy();self.refresh()
  ttk.Button(f,text="Save Kit",style="Primary.TButton",command=save).grid(row=7,column=1,sticky="e",pady=(12,0))
 def delete(self):
  k=self.selected();
  if not k:return
  if messagebox.askyesno("Delete kit",f'Delete {k["name"]}?'):
   try:self.api.delete_kit(k["id"]);self.refresh()
   except ApiError as e:theme.show_error(self,"Delete failed",str(e))
