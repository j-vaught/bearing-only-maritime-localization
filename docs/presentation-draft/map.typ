#import "@preview/cetz:0.5.2": canvas, draw

#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#let green = rgb("#2ca02c")
#let red = rgb("#d62728")
#let purple = rgb("#9467bd")
#let garnet = rgb("#73000A")
#let water-color = rgb("#edf4f8")
#let land-color = rgb("#c1c5c8")

#let label(p, body, size: 12pt, anchor: "center", fill: black) = draw.content(p,
  box(fill: white, inset: 0.7mm)[#text(size: size, fill: fill)[#body]], anchor: anchor)

#let symbol(p, kind: "aid", r: 0.8) = {
  import draw: *
  let (x, y) = p
  if kind == "aid" {
    rect((x - r,y - r),(x+r,y+r),fill: blue,stroke: 0.25pt+white)
  } else if kind == "ais" {
    line((x,y+r*1.4),(x - r*1.2,y - r),(x+r*1.2,y - r),close:true,fill:orange,stroke:0.3pt+black)
  } else if kind == "observer" {
    circle(p,radius:r*1.8,fill:white,stroke:1pt+black)
    circle(p,radius:r*0.7,fill:black,stroke:none)
  } else if kind == "truth" {
    line((x - r,y - r),(x+r,y+r),stroke:1.5pt+black)
    line((x - r,y+r),(x+r,y - r),stroke:1.5pt+black)
  } else if kind == "estimate" {
    line((x,y+r),(x - r,y),(x,y - r),(x+r,y),close:true,fill:purple,stroke:0.6pt+black)
  }
}

#let mapper(m, origin, w, h) = {
  let (xmin,ymin,xmax,ymax) = m.bounds
  let s = calc.min(w/(xmax - xmin),h/(ymax - ymin))
  let ox = origin.at(0)+(w - (xmax - xmin)*s)/2
  let oy = origin.at(1)+(h - (ymax - ymin)*s)/2
  (point: p => (ox+(p.at(0) - xmin)*s,oy+(p.at(1) - ymin)*s),
   scale:s,box:(ox,oy,ox+(xmax - xmin)*s,oy+(ymax - ymin)*s))
}

#let basemap(m, origin:(0,0), w:120,h:120,points:true, observer:true, grid-step:none, labels:true, point-radius:0.7, rays:(), ranges:(), title:none) = {
  import draw: *
  let tr=mapper(m,origin,w,h)
  let xy=tr.point
  let b=tr.box
  rect((b.at(0),b.at(1)),(b.at(2),b.at(3)),fill:white,stroke:0.5pt+black)
  for (layer,col) in (("water",water-color),("land",land-color)) {
    for poly in m.at(layer,default:()) {
      compound-path({
        for ring in poly { if ring.len() > 2 { line(..ring.map(xy),close:true) } }
      },fill:col,stroke:none,fill-rule:"even-odd")
    }
  }
  if grid-step != none {
    let (xmin,ymin,xmax,ymax)=m.bounds
    for x in range(int(calc.ceil(xmin/grid-step)),int(calc.floor(xmax/grid-step))+1) {
      line(xy((x*grid-step,ymin)),xy((x*grid-step,ymax)),stroke:0.25pt+rgb("#a6afb5"))
    }
    for y in range(int(calc.ceil(ymin/grid-step)),int(calc.floor(ymax/grid-step))+1) {
      line(xy((xmin,y*grid-step)),xy((xmax,y*grid-step)),stroke:0.25pt+rgb("#a6afb5"))
    }
  }
  for ray in rays {
    line(xy(ray.at(0)),xy(ray.at(1)),stroke:0.7pt+blue.transparentize(25%))
  }
  if m.observer != none {
    for radius in ranges {
      let rings=m.at("range_circles",default:()).filter(c=>c.radius_m==radius)
      if rings.len() > 0 {
        line(..rings.first().xy.map(xy),close:true,stroke:(paint:blue,thickness:0.65pt,dash:"dashed"))
      } else {
        circle(xy(m.observer),radius:radius*tr.scale,fill:none,stroke:(paint:blue,thickness:0.65pt,dash:"dashed"))
      }
    }
  }
  if points {
    for p in m.aids { symbol(xy(p),r:point-radius) }
    for p in m.at("unknown_objects",default:()) {
      let v=xy(p)
      rect((v.at(0) - point-radius,v.at(1) - point-radius),(v.at(0)+point-radius,v.at(1)+point-radius),fill:white,stroke:0.35pt+blue)
    }
    for p in m.ais { symbol(xy(p),kind:"ais",r:point-radius*1.2) }
  }
  if observer and m.observer != none { symbol(xy(m.observer),kind:"observer",r:1) }
  if labels {
    let sm=m.at("scale_m",default:1000)
    let sx=b.at(0)+5
    let sy=b.at(1)+5
    line((sx,sy),(sx+sm*tr.scale,sy),stroke:1.2pt+black)
    line((sx,sy - 0.7),(sx,sy+0.7),stroke:0.8pt+black)
    line((sx+sm*tr.scale,sy - 0.7),(sx+sm*tr.scale,sy+0.7),stroke:0.8pt+black)
    label((sx,sy+3),if sm>=1000 {str(sm/1000)+" km"} else {str(sm)+" m"},size:9pt,anchor:"west")
    let nx=b.at(2) - 5
    let ny=b.at(3) - 14
    line((nx,ny),(nx,ny+8),stroke:0.8pt+black,mark:(end:"stealth",fill:black))
    label((nx,ny+11),[N#sub[G]],size:9pt)
  }
  if title != none { label((b.at(0)+2,b.at(3) - 2),title,size:14pt,anchor:"north-west") }
}

#let legend(origin:(0,0), scale:true) = {
  import draw: *
  let (x,y)=origin
  symbol((x,y),r:1)
  content((x+4,y),text(size:11pt)[Chart aid],anchor:"west")
  symbol((x+39,y),kind:"ais",r:1)
  content((x+43,y),text(size:11pt)[AIS],anchor:"west")
  rect((x+63,y - 1.5),(x+67,y+1.5),fill:land-color,stroke:none)
  content((x+70,y),text(size:11pt)[Land],anchor:"west")
  rect((x+94,y - 1.5),(x+98,y+1.5),fill:water-color,stroke:0.35pt+black)
  content((x+101,y),text(size:11pt)[Water],anchor:"west")
  rect((x+126,y - 1.5),(x+130,y+1.5),fill:white,stroke:0.35pt+black)
  content((x+133,y),text(size:11pt)[Unknown],anchor:"west")
}

#let polar(measurements, origin:(0,0),r:46,mode:"all",reveal:1) = {
  import draw: *
  let (x,y)=origin
  for radius in (r/2,r) { circle(origin,radius:radius,stroke:0.45pt+rgb("#aaaaaa"),fill:none) }
  line((x - r,y),(x+r,y),stroke:0.35pt+rgb("#aaaaaa"))
  line((x,y - r),(x,y+r),stroke:0.35pt+rgb("#aaaaaa"))
  for (i,m) in measurements.enumerate() {
    if i < calc.ceil(measurements.len()*reveal) and (mode=="all" or m.object_type==mode) {
      let a=m.bearing_deg*1deg
      let col=if m.object_type=="vessel"{orange}else{blue}
      line(origin,(x+r*calc.sin(a),y+r*calc.cos(a)),stroke:0.8pt+col)
    }
  }
  circle(origin,radius:1,fill:black,stroke:none)
  for (p,t) in (((x,y+r+5),"N · 0°"),((x+r+6,y),"90°"),((x,y - r - 5),"180°"),((x - r - 7,y),"270°")) {
    content(p,text(size:10pt)[#t])
  }
}
