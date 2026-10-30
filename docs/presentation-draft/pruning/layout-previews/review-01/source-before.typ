#import "@preview/cetz:0.5.2": canvas, draw
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
  let oy = origin.at(1) + (h - (y1 - y0) * scale) / 2
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
    let p=xy(stage.observer)
    line((p.at(0) - 1.2,p.at(1) - 1.2),(p.at(0) + 1.2,p.at(1) + 1.2),stroke:1pt + black)
    line((p.at(0) - 1.2,p.at(1) + 1.2),(p.at(0) + 1.2,p.at(1) - 1.2),stroke:1pt + black)
    let e=xy(stage.estimate)
    line((e.at(0),e.at(1) + 1.2),(e.at(0) - 1.2,e.at(1)),(e.at(0),e.at(1) - 1.2),
      (e.at(0) + 1.2,e.at(1)),close:true,fill:purple,stroke:0.45pt + black)
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
  let nx=b.at(2) - 4
  let ny=b.at(3) - 12
  line((nx,ny),(nx,ny + 7),stroke:0.7pt + black,mark:(end:"stealth",fill:black))
  label((nx,ny + 10),[N#sub[G]],size:8pt)
}
#let figure(stage,body:false) = {
  let W=if body {276}else{320}
  let H=if body {128}else{180}
  let lpos=if body {(0,15)}else{(12,22)}
  let rpos=if body {(124,15)}else{(156,22)}
  let lw=if body {110}else{130}
  let rw=152
  let ph=if body {105}else{132}
  set page(width:W * 1mm,height:H * 1mm,margin:0mm,fill:white)
  place(top + left,canvas(length:1mm,{
    import draw: *
    rect((0,0),(W,H),fill:white,stroke:none)
    pane(stage,"coast",lpos,lw,ph)
    pane(stage,"harbor",rpos,rw,ph)
    label((lpos.at(0),lpos.at(1) + ph + 2),[Eastern Seaboard],size:if body {11pt}else{12pt},anchor:"south-west")
    label((rpos.at(0),rpos.at(1) + ph + 2),stage.right_title,size:if body {11pt}else{12pt},anchor:"south-west")
    if not body {
      label((12,174),stage.title + " · " + stage.width_label,size:23pt,anchor:"west")
      label((12,164),if stage.final {str(stage.status_counts.at("0"))+" consistent · "+str(stage.status_counts.at("1"))+" inconsistent · "+str(stage.status_counts.at("2"))+" unresolved"}
        else {str(stage.counts.retained) + " retained · " + str(stage.counts.rejected) + " rejected at this scale"},
        size:11pt,anchor:"west")
    }
    let ly=if body {7}else{12}
    if stage.final {
      status-symbol((8,ly),0,r:0.8)
      label((11,ly),[Consistent],size:9pt,anchor:"west")
      status-symbol((45,ly),1,r:0.9)
      label((48,ly),[Inconsistent],size:9pt,anchor:"west")
      status-symbol((88,ly),2,r:0.9)
      label((91,ly),[Unresolved],size:9pt,anchor:"west")
      label((134,ly),"× Truth     ◆ Selected estimate",size:9pt,anchor:"west")
    } else {
      rect((8,ly - 1),(11,ly + 1),fill:blue,stroke:none)
      label((14,ly),[Retained],size:9pt,anchor:"west")
      rect((48,ly - 1),(51,ly + 1),fill:orange,stroke:none)
      label((54,ly),[Rejected at this scale],size:9pt,anchor:"west")
    }
    if not body {
      label((12,4),"Scale-ordered actual trace · display pixels aggregate small cells · playback is not search timing",
        size:8pt,anchor:"west")
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
