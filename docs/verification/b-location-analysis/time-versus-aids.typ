#import "plot-common.typ": *
#set page(width:320mm,height:150mm,margin:0mm,fill:white)
#set text(font:"Arial",size:9pt)
#set document(author:"J.C. Vaught",title:"Coast-wide search time versus detected charting aids")
#let rows=json("runtime.json").rows.filter(r=>r.aids>15)
#place(top+left,canvas(length:1mm,{
  import draw: *
  rect((0,0),(320,150),fill:white,stroke:none)
  content((160,144),text(size:14pt)[Search time versus detected charting aids])
  content((160,133),key())
  let a=axes((20,26),(125,85),(15,105),(5,850),(20,40,60,80,100),(5,10,30,100,300,600),[Detected charting aids],[Search time (s), logarithmic],logy:true)
  let b=axes((178,26),(125,85),(15,105),(7,18),(20,40,60,80,100),(8,10,12,14,16,18),[Detected charting aids],[Search time (s), detail])
  a.drawing
  b.drawing
  let labels=(B2:(-4,5),B4:(3,4),B7:(4,0),B11:(6,1),B16:(6,0),B18:(5,0))
  let detail=(B2:(5,6),B4:(2,4),B5:(3,2),B6:(3,0),B8:(-3,3),B9:(-5,3),B12:(4,2),B13:(0,3),B14:(-3,3),B15:(1,-4),B17:(-6,-4),B19:(-2,3),B20:(-4,-2),B21:(0,-4),B22:(4,2),B24:(-4,2))
  for r in rows {
    let p=((a.x)(r.aids),(a.y)(r.plot_search_s))
    symbol(p,r.status)
    if r.site in labels {
      let d=labels.at(r.site)
      content((p.at(0)+d.at(0),p.at(1)+d.at(1)),text(size:8pt)[#r.site])
    }
    if r.status == "timeout" {line((p.at(0),p.at(1)+1.5),(p.at(0),p.at(1)+5),stroke:0.7pt+red,mark:(end:"stealth",fill:red))}
    if r.plot_search_s >=7 and r.plot_search_s <=18 {
      let q=((b.x)(r.aids),(b.y)(r.plot_search_s))
      symbol(q,r.status)
      let d=detail.at(r.site)
      let label=(q.at(0)+d.at(0),q.at(1)+d.at(1))
      line(q,label,stroke:0.3pt+rgb("#888888"))
      content(label,text(size:8pt)[#r.site])
    }
  }
  content((160,5),text(size:8pt)[20 eligible sites · GPUs 0–2 · Search only; preparation, simulation and selection excluded · B11 stopped at 600 s])
}))
