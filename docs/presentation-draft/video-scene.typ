#import "figures.typ": scene-polar
#set page(width:320mm,height:180mm,margin:0mm,fill:white)
#set text(font:"Arial",fill:black)
#let reveal=float(sys.inputs.at("reveal",default:"1"))
#place(top+left,dx:18mm,dy:11mm,text(size:27pt,weight:"bold")[Mapped scene → bearing observations])
#place(top+left,dx:18mm,dy:30mm,line(length:284mm,stroke:0.65pt+rgb("#73000A")))
#place(top+left,dx:22mm,dy:36mm,scene-polar(reveal:reveal))
#place(bottom+left,dx:18mm,dy:-6mm,text(size:11pt)[Observation reveal · one saved simulated frame on NOAA geometry])
