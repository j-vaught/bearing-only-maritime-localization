#import "@preview/cetz:0.5.2": canvas, draw
#set page(width: 88mm, height: 83mm, margin: 0mm)
#set text(font: "Arial", size: 7pt)
#set document(author:"J.C. Vaught",title:"Saved terminal centers near selected estimate")
#let data=json("centers.json")
#let cols=(rgb("#1f77b4"),rgb("#ff7f0e"),rgb("#2ca02c"))
#canvas(length:1mm,{
 import draw: *
 let x(v)=45+2.0*v
 let y(v)=42+2.0*v
 for tick in (-10,-5,0,5,10) {
  line((x(tick),16),(x(tick),68),stroke:0.25pt+rgb("#dddddd"))
  line((19,y(tick)),(71,y(tick)),stroke:0.25pt+rgb("#dddddd"))
  content((x(tick),13),[#tick])
  content((16,y(tick)),[#tick],anchor:"east")
 }
 line((19,16),(74,16),stroke:0.6pt,mark:(end:"stealth",fill:black))
 line((19,16),(19,71),stroke:0.6pt,mark:(end:"stealth",fill:black))
 for p in data.centers {rect((x(p.x)-0.48,y(p.y)-0.48),(x(p.x)+0.48,y(p.y)+0.48),fill:cols.at(p.status),stroke:none)}
 let tx=x(data.truth.at(0));let ty=y(data.truth.at(1))
 line((tx - 1.3,ty - 1.3),(tx + 1.3,ty + 1.3),stroke:0.9pt)
 line((tx - 1.3,ty + 1.3),(tx + 1.3,ty - 1.3),stroke:0.9pt)
 rect((x(0)-1.0,y(0)-1.0),(x(0)+1.0,y(0)+1.0),fill:none,stroke:0.8pt)
 content((45,6.8),[East offset from estimate (m)])
 content((19,76),[North offset (m)],anchor:"west")
 for (i,label) in ("148 consistent","67 inconsistent","182 unresolved").enumerate() {
  let xx=4+i*28.4
  rect((xx,1),(xx+1.4,2.4),fill:cols.at(i),stroke:none)
  content((xx+2.5,1.7),text(size:6pt)[#label],anchor:"west")
 }
})
