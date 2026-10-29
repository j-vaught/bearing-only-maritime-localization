#import "@preview/cetz:0.5.2": canvas, draw
#import "@preview/lilaq:0.6.0" as lq
#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#let red = rgb("#d62728")
#let gray = rgb("#dddddd")
#let symbol(p, status, r: 1.1) = {
  import draw: *
  let (x,y) = p
  if status == "completed" {
    rect((x - r,y - r),(x+r,y+r),fill:blue,stroke:0.3pt+white)
  } else if status == "failed" {
    line((x - r,y - r),(x+r,y+r),stroke:1.1pt+orange)
    line((x - r,y+r),(x+r,y - r),stroke:1.1pt+orange)
  } else if status == "timeout" {
    line((x,y+r*1.3),(x - r*1.2,y - r),(x+r*1.2,y - r),close:true,fill:red,stroke:none)
  } else {
    rect((x - r,y - r),(x+r,y+r),fill:white,stroke:0.6pt+black)
  }
}
#let sample(status) = canvas(length:1mm,{
  draw.rect((0,0),(5,3),fill:none,stroke:none)
  symbol((2.5,1.5),status,r:0.9)
})
#let key() = {
  set text(font:"Arial",size:9pt)
  set grid(columns:6,column-gutter:4pt,row-gutter:2pt,align:horizon+left)
  lq.legend(sample("completed"),[Certified estimate],sample("failed"),[No certified estimate],sample("timeout"),[600 s timeout],fill:white,stroke:none,inset:0pt,radius:0pt)
}
#let axes(origin, size, xb, yb, xticks, yticks, xlabel, ylabel, logy:false, logx:false) = {
  import draw: *
  let (ox,oy)=origin
  let (w,h)=size
  let xx(v)=ox+w*(if logx {calc.log(v,base:10)-calc.log(xb.at(0),base:10)}else{v - xb.at(0)})/(if logx{calc.log(xb.at(1)/xb.at(0),base:10)}else{xb.at(1)-xb.at(0)})
  let yy(v)=oy+h*(if logy {calc.log(v,base:10)-calc.log(yb.at(0),base:10)}else{v - yb.at(0)})/(if logy{calc.log(yb.at(1)/yb.at(0),base:10)}else{yb.at(1)-yb.at(0)})
  let elements = {
  for t in xticks {
    line((xx(t),oy),(xx(t),oy+h),stroke:0.3pt+gray)
    content((xx(t),oy - 3.5),text(size:8pt)[#t])
  }
  for t in yticks {
    line((ox,yy(t)),(ox+w,yy(t)),stroke:0.3pt+gray)
    content((ox - 2,yy(t)),text(size:8pt)[#t],anchor:"east")
  }
  line((ox,oy),(ox+w+2,oy),stroke:0.65pt+black,mark:(end:"stealth",fill:black))
  line((ox,oy),(ox,oy+h+2),stroke:0.65pt+black,mark:(end:"stealth",fill:black))
  content((ox+w/2,oy - 10),text(size:9pt)[#xlabel])
  content((ox,oy+h+7),text(size:9pt)[#ylabel],anchor:"west")
  }
  (drawing:elements,x:xx,y:yy)
}
