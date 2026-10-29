#import "@preview/cetz:0.5.2": canvas, draw
#set page(width: 180mm, height: 64mm, margin: 0mm)
#set text(font: "Arial", size: 8pt)
#set document(author: "J.C. Vaught", title: "Observed pruning by subdivision depth")
#let rows = json("stages.json")
#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#canvas(length: 1mm, {
  import draw: *
  let x(d) = 16 + 11 * d
  let y(v) = 12 + 8.2 * calc.log(v, base: 10)
  for p in range(5) {
    let yy = y(calc.pow(10,p))
    line((16,yy),(170,yy),stroke: 0.3pt+rgb("#cccccc"))
    content((13,yy),[$10^#p$],anchor:"east")
  }
  line((16,12),(173,12),stroke:0.6pt,mark:(end:"stealth",fill:black))
  line((16,12),(16,54),stroke:0.6pt,mark:(end:"stealth",fill:black))
  for d in range(15) {
    line((x(d),12),(x(d),10.8),stroke:0.5pt)
    content((x(d),8.4),[#d])
  }
  for (key,col) in (("evaluated",blue),("retained",orange)) {
    line(..rows.map(r=>(x(r.depth),y(r.at(key)))),stroke:1.1pt+col)
    for r in rows {rect((x(r.depth)-0.6,y(r.at(key))-0.6),(x(r.depth)+0.6,y(r.at(key))+0.6),fill:col,stroke:none)}
  }
  content((93,2.8),[Subdivision depth])
  content((16,59),[Cell count, logarithmic scale],anchor:"west")
  line((91,58),(99,58),stroke:1.1pt+blue)
  content((101,58),[Evaluated],anchor:"west")
  line((131,58),(139,58),stroke:1.1pt+orange)
  content((141,58),[Retained],anchor:"west")
})
