#set page(width:320mm,height:180mm,margin:0mm,fill:white)
#set text(font:"Arial",fill:black)
#let stage=sys.inputs.at("stage",default:"02")
#let title=if stage=="02" {"Full search domain"} else if stage=="29" {"Selected Charleston position"} else {"Recorded terminal centers"}
#place(top+left,dx:18mm,dy:11mm,text(size:27pt,weight:"bold")[#title])
#place(top+left,dx:18mm,dy:30mm,line(length:284mm,stroke:0.65pt+rgb("#73000A")))
#place(top+left,dx:18mm,dy:36mm,image("figures/"+stage+".png",width:284mm))
#place(bottom+left,dx:18mm,dy:-6mm,text(size:11pt)[Saved result overview · geographic views, not intermediate pruning stages])
