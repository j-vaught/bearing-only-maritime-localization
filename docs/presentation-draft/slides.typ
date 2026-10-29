#set page(width:320mm,height:180mm,margin:0mm,fill:white)
#set text(font:"Arial",size:20pt,fill:black)
#set document(title:"Bearing-only maritime localization",author:"J.C. Vaught",description:"Technical review draft. Real geographic inputs and saved simulated observations.")

#let deck=json("slides.json")
#let slides=deck.main+deck.backup
#let video-slides=("13","27")

#for (i,slide) in slides.enumerate() {
  let id=if type(slide.number)==int {if slide.number < 10 {"0"+str(slide.number)} else {str(slide.number)}} else {slide.number}
  let title-size=if slide.title.len()>55 {24pt} else {27pt}
  place(top+left,dx:18mm,dy:11mm,block(width:284mm)[#text(size:title-size,weight:"bold")[#slide.title]])
  place(top+left,dx:18mm,dy:30mm,line(length:284mm,stroke:0.65pt+rgb("#73000A")))
  place(top+left,dx:18mm,dy:36mm,image("figures/"+id+".png",width:284mm))
  if id=="01" {
    place(bottom+left,dx:18mm,dy:-6mm,text(size:12pt)[J.C. Vaught])
  } else if id in video-slides {
    let caption=if id=="13" {[VIDEO PLACEHOLDER · saved scene → bearing observations]} else {[VIDEO PLACEHOLDER · synchronized pruning trace]}
    place(bottom+left,dx:18mm,dy:-5mm,text(size:11pt,fill:rgb("#73000A"))[#caption])
  } else if id=="20" {
    place(bottom+left,dx:18mm,dy:-5mm,text(size:10pt)[Real map/catalog crops · only Charleston has the completed localization result shown here.])
  } else if id=="31" {
    place(bottom+left,dx:18mm,dy:-5mm,text(size:10pt)[Proposed evaluation · current result uses chart-aid bearings.])
  } else if id=="29" or id=="32" or id=="33" {
    place(bottom+left,dx:18mm,dy:-5mm,text(size:10pt)[Simulated observations on archived NOAA geometry.])
  }
  place(bottom+right,dx:-18mm,dy:-5mm,text(size:11pt,fill:rgb("#666666"))[#slide.number])
  if i < slides.len() - 1 {pagebreak()}
}
