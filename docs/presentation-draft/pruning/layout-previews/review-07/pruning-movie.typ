#import "@preview/cetz:0.5.2": canvas, draw
#import "@preview/lilaq:0.6.0" as lq
#let mode = sys.inputs.at("mode", default: "storyboard")
#let data-file = sys.inputs.at("data-file", default: none)
#let data = if data-file == none {none}else{json(data-file)}
#let blue = rgb("#1f77b4")
#let orange = rgb("#ff7f0e")
#let red = rgb("#d62728")
#let green = rgb("#2ca02c")
#let purple = rgb("#9467bd")
#let water = rgb("#edf4f8")
#let land = rgb("#c1c5c8")
#set text(font: "Arial", fill: black)
#set par(spacing: 0pt)

#let mapping(bounds, origin, w, h) = {
  let (x0,y0,x1,y1) = bounds
  let scale = calc.min(w / (x1 - x0),h / (y1 - y0))
  let ox = origin.at(0) + (w - (x1 - x0) * scale) / 2
  let oy = origin.at(1) + h - (y1 - y0) * scale
  (xy: p => (ox + (p.at(0) - x0) * scale,oy + (p.at(1) - y0) * scale),
   scale: scale, box: (ox,oy,ox + (x1 - x0) * scale,oy + (y1 - y0) * scale))
}
#let background(data) = {
  let w = data.native_width_mm
  let h = data.native_height_mm
  set page(width:w * 1mm,height:h * 1mm,margin:0mm,fill:white)
  place(top + left,canvas(length:1mm,{
    import draw: *
    let tr = mapping(data.bounds,(0,0),w,h)
    rect((0,0),(w,h),fill:white,stroke:none)
    for (layer,col) in (("water",water),("land",land),("unknown",white)) {
      for poly in data.at(layer,default:()) {
        compound-path({
          for ring in poly { if ring.len() > 2 { line(..ring.map(tr.xy),close:true) } }
        },fill:col,stroke:none,fill-rule:"even-odd")
      }
    }
  }))
}
#let label(p,body,size:10pt,anchor:"center",fill:black) = {
  draw.content(p,text(size:size,fill:fill)[#body],anchor:anchor)
}
#let status-symbol(p,status,r:0.65) = {
  import draw: *
    let (x,y)=p
  if status == 0 {
    circle((x,y),radius:r,fill:green,stroke:0.2pt + white)
  } else if status == 1 {
    line((x - r,y - r),(x + r,y + r),stroke:0.7pt + red)
    line((x - r,y + r),(x + r,y - r),stroke:0.7pt + red)
  } else {
    circle((x,y),radius:r,fill:white,stroke:0.6pt + purple)
  }
}
#let commas(n) = {
  let s=str(n)
  let parts=()
  for i in range(s.len(),0,step:-3) {parts.push(s.slice(calc.max(i - 3,0),i))}
  parts.rev().join(",")
}
#let legend-sample(kind) = canvas(length:1mm,{
  draw.rect((0,0),(5,3),fill:none,stroke:none)
  if kind == "retained" {draw.rect((0,0.5),(4,2.5),fill:blue,stroke:none)}
  else if kind == "rejected" {draw.rect((0,0.5),(4,2.5),fill:orange,stroke:none)}
  else if kind == "truth" {
    draw.line((1,0.5),(3,2.5),stroke:0.8pt + black)
    draw.line((1,2.5),(3,0.5),stroke:0.8pt + black)
  } else if kind == "estimate" {
    draw.line((2,2.7),(0.8,1.5),(2,0.3),(3.2,1.5),close:true,fill:purple,stroke:0.3pt + black)
  } else {status-symbol((2,1.5),kind,r:0.9)}
})
#let map-legend(stage) = {
  set text(font:"Arial",size:10pt)
  set grid(columns:2,column-gutter:4pt,row-gutter:3pt,align:horizon + left)
  let entries=if stage.final {
    (legend-sample(0),[Consistent],legend-sample(1),[Inconsistent],legend-sample(2),[Unresolved],legend-sample("truth"),[True position],legend-sample("estimate"),[Selected estimate])
  } else {
    (legend-sample("retained"),[Retained],legend-sample("rejected"),[Rejected],legend-sample("truth"),[True position])
  }
  lq.legend(..entries,fill:white,stroke:0.5pt + black,inset:5pt,radius:0pt)
}
#let compass(c,angle:90) = {
  import draw: *
  for k in range(8) {
    let a=(angle - k*45)*1deg
    let r=if calc.rem(k,2)==0 {4.8}else{3.0}
    line(c,(c.at(0)+r*calc.cos(a),c.at(1)+r*calc.sin(a)),stroke:0.5pt + black,
      mark:(end:"stealth",fill:black,length:if calc.rem(k,2)==0 {2.5}else{1.6},width:if calc.rem(k,2)==0 {1.6}else{1.0}))
  }
  circle(c,radius:0.35,fill:black,stroke:none)
  for (k,t) in ((0,"N"),(2,"E"),(4,"S"),(6,"W")) {
    let a=(angle - k*45)*1deg
    label((c.at(0)+7*calc.cos(a),c.at(1)+7*calc.sin(a)),t,size:9pt)
  }
}
#let pane(stage,name,origin,w,h) = {
  import draw: *
    let data=stage.at(name)
  let tr=mapping(data.bounds,origin,w,h)
  let xy=tr.xy
  let b=tr.box
  content((b.at(0),b.at(1)),image(data.background,width:(b.at(2) - b.at(0)) * 1mm,
    height:(b.at(3) - b.at(1)) * 1mm),anchor:"south-west")
  if name == "harbor" and data.bounds.at(2) - data.bounds.at(0) < 200 {
    let step=if data.bounds.at(2) - data.bounds.at(0) < 60 {5}else{20}
    let (x0,y0,x1,y1)=data.bounds
    for k in range(int(calc.ceil(x0 / step)),int(calc.floor(x1 / step)) + 1) {
      line(xy((k * step,y0)),xy((k * step,y1)),stroke:0.25pt + rgb("#b2bbc0"))
    }
    for k in range(int(calc.ceil(y0 / step)),int(calc.floor(y1 / step)) + 1) {
      line(xy((x0,k * step)),xy((x1,k * step)),stroke:0.25pt + rgb("#b2bbc0"))
    }
  }
  for cell in data.rejected {
    rect(xy((cell.at(0),cell.at(1))),xy((cell.at(2),cell.at(3))),
      fill:orange.transparentize(65%),stroke:none)
  }
  if not stage.final or name == "coast" {
    for cell in data.retained {
      rect(xy((cell.at(0),cell.at(1))),xy((cell.at(2),cell.at(3))),
        fill:blue.transparentize(if stage.generation == none {78%}else if name == "coast" {0%}else{62%}),stroke:none)
    }
  }
  if name == "coast" {
    let r=stage.harbor.bounds
    let a=xy((r.at(0),r.at(1)))
    let z=xy((r.at(2),r.at(3)))
    let cx=(a.at(0) + z.at(0)) / 2
    let cy=(a.at(1) + z.at(1)) / 2
    let rw=calc.max(z.at(0) - a.at(0),0.8)
    let rh=calc.max(z.at(1) - a.at(1),0.8)
    rect((cx - rw / 2,cy - rh / 2),(cx + rw / 2,cy + rh / 2),fill:none,stroke:0.75pt + black)
  }
  if stage.final and name == "harbor" {
    for candidate in stage.terminal_centers { status-symbol(xy(candidate.xy),candidate.status) }
    let e=xy(stage.estimate)
    line((e.at(0),e.at(1) + 1.2),(e.at(0) - 1.2,e.at(1)),(e.at(0),e.at(1) - 1.2),
      (e.at(0) + 1.2,e.at(1)),close:true,fill:purple,stroke:0.45pt + black)
  }
  let truth=xy(stage.observer)
  for weight in (2.5pt,0.9pt) {
    let color=if weight==2.5pt {white}else{black}
    line((truth.at(0) - 1.1,truth.at(1) - 1.1),(truth.at(0)+1.1,truth.at(1)+1.1),stroke:weight + color)
    line((truth.at(0) - 1.1,truth.at(1)+1.1),(truth.at(0)+1.1,truth.at(1) - 1.1),stroke:weight + color)
  }
  rect((b.at(0),b.at(1)),(b.at(2),b.at(3)),fill:none,stroke:0.55pt + black)
  let extent=data.bounds.at(2) - data.bounds.at(0)
  let raw=extent / 5
  let power=calc.pow(10,calc.floor(calc.log(raw,base:10)))
  let scale=if raw / power >= 5 {5 * power}else if raw / power >= 2 {2 * power}else{power}
  let sx=b.at(0) + 4
  let sy=b.at(1) + 4
  line((sx,sy),(sx + scale * tr.scale,sy),stroke:1pt + black)
  line((sx,sy - 0.6),(sx,sy + 0.6),stroke:0.7pt + black)
  line((sx + scale * tr.scale,sy - 0.6),(sx + scale * tr.scale,sy + 0.6),stroke:0.7pt + black)
  label((sx,sy + 2.5),if scale >= 1000 {str(scale / 1000) + " km"}else{str(scale) + " m"},size:8pt,anchor:"west")
  compass((b.at(2) - 12,b.at(3) - 13),angle:data.at("north_angle_deg",default:90))
  content((b.at(2) - 3,b.at(1) + 3),map-legend(stage),anchor:"south-east")
}
#let figure(stage,body:false) = {
  let W=if body {276}else{320}
  let H=if body {128}else{180}
  let lpos=if body {(0,5)}else{(12,8)}
  let rpos=if body {(124,5)}else{(156,8)}
  let lw=if body {110}else{130}
  let rw=152
  let ph=if body {110}else{148}
  let left-box=mapping(stage.coast.bounds,lpos,lw,ph).box
  let right-box=mapping(stage.harbor.bounds,rpos,rw,ph).box
  set page(width:W * 1mm,height:H * 1mm,margin:0mm,fill:white)
  place(top + left,canvas(length:1mm,{
    import draw: *
    rect((0,0),(W,H),fill:white,stroke:none)
    pane(stage,"coast",lpos,lw,ph)
    pane(stage,"harbor",rpos,rw,ph)
    label(((left-box.at(0)+left-box.at(2))/2,left-box.at(3)+4),[Eastern Seaboard],size:if body {12pt}else{16pt})
    label(((right-box.at(0)+right-box.at(2))/2,right-box.at(3)+4),[Charleston],size:if body {12pt}else{16pt})
    if not body {
      let width=stage.at("cell_size_label",default:stage.width_label.replace(" roots",""))
      let counts=stage.at("display_counts",default:stage.counts)
      let metrics-x=(right-box.at(0)+right-box.at(2))/2
      label((metrics-x,right-box.at(1) - 8),[Cell size #width],size:16pt)
      label((metrics-x,right-box.at(1) - 17),[#commas(counts.retained) retained  ·  #commas(counts.rejected) rejected],size:16pt)
    }
  }))
}

#if data != none and mode == "background" {
  background(data)
} else if data != none and mode == "storyboard" {
  for (i,file) in data.stages.enumerate() {
    if i > 0 {pagebreak()}
    figure(json(file))
  }
} else if data != none {
  figure(data,body:mode == "body")
}
