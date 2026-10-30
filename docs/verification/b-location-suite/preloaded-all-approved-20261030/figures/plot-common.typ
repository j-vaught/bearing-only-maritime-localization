#import "@preview/cetz:0.5.2": canvas, draw
#import "@preview/lilaq:0.6.0" as lq
#let blue = rgb("#1f77b4")
#let gray = rgb("#dddddd")
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
