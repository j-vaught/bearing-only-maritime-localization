// GPU whole-cell physical-marker incidence and exact injective feasibility.
// The host-test switch exists only for compiling this same matcher in CPU tests;
// the runtime Python API always launches CUDA and has no host fallback.
#define DISTINCT_INT_MAX 2147483647
#ifdef RESIDENT_DISTINCT_HOST_TEST
#define __device__
#define DISTINCT_WIDTH 1
static inline void distinct_sync() {}
static inline int distinct_min(int value) { return value; }
static inline bool distinct_any(bool value) { return value; }
static inline unsigned int distinct_or(unsigned int* address,unsigned int value) {
    unsigned int old=*address;*address|=value;return old;
}
static inline int distinct_first(unsigned int value) { return __builtin_ffs(value)-1; }
#else
#define DISTINCT_WIDTH 32
__device__ inline void distinct_sync() { __syncwarp(0xffffffffu); }
__device__ inline int distinct_min(int value) {
    for(int offset=16;offset;offset/=2)
        value=min(value,__shfl_down_sync(0xffffffffu,value,offset));
    return __shfl_sync(0xffffffffu,value,0);
}
__device__ inline bool distinct_any(bool value) { return __any_sync(0xffffffffu,value); }
__device__ inline unsigned int distinct_or(unsigned int* address,unsigned int value) {
    return atomicOr(address,value);
}
__device__ inline int distinct_first(unsigned int value) { return __ffs(value)-1; }
#endif

// Parent carries the physical positive-report contract. A detection originates
// from its representative object's true bearing plus bounded sensor error, not
// from an arbitrary point in a permissive nominal grouped-extent gap.
__device__ inline bool distinct_physical_edge(double angle,double expansion,double extent,
    double report,double tolerance,double bearing_error) {
    return fabs(remainder(angle-report,360.))<=tolerance+expansion+extent+bearing_error;
}

// One warp per graph, with candidate columns scanned cooperatively. Every
// augmenting-path breadth-first search visits each column at most once. Return
// 0=full injection, 2=unsupported report, 3=count, 4=Hall deficiency, -1=retain.
// The graph is object-major, each column holding ceil(reports/32) report bits.
__device__ int distinct_match_graph(const unsigned int* graph,int reports,int columns,int words,
    int distinct_proofs,int lane,int* owners,int* parents,int* row_matches,
    unsigned int* reached,unsigned int* frontier,unsigned int* next,int* matched) {
    if(lane==0)*matched=0;
    for(int col=lane;col<columns;col+=DISTINCT_WIDTH)owners[col]=-1;
    for(int row=lane;row<reports;row+=DISTINCT_WIDTH)row_matches[row]=-1;
    distinct_sync();
    for(int row=0;row<reports;++row) {
        bool supported=false;
        for(int col=lane;col<columns;col+=DISTINCT_WIDTH)
            supported|=(graph[col*words+row/32]&(1u<<(row%32)))!=0;
        if(!distinct_any(supported))return 2;
    }
    if(reports==0)return 0;
    // Preserve the existing identity gate on count and injective proofs.
    if(!distinct_proofs)return -1;
    if(columns<reports)return 3;
    for(int root=0;root<reports;++root) {
        for(int col=lane;col<columns;col+=DISTINCT_WIDTH)parents[col]=-1;
        for(int word=lane;word<words;word+=DISTINCT_WIDTH)
            reached[word]=frontier[word]=next[word]=0u;
        distinct_sync();
        if(lane==0)reached[root/32]=frontier[root/32]=1u<<(root%32);
        distinct_sync();
        bool augmented=false;
        for(int level=0;level<=reports;++level) {
            int free_column=DISTINCT_INT_MAX;
            for(int col=lane;col<columns;col+=DISTINCT_WIDTH) {
                if(parents[col]>=0)continue;
                int predecessor=-1;
                for(int word=0;word<words;++word) {
                    unsigned int common=graph[col*words+word]&frontier[word];
                    if(common) { predecessor=word*32+distinct_first(common);break; }
                }
                if(predecessor<0)continue;
                parents[col]=predecessor;
                int owner=owners[col];
                if(owner<0)free_column=free_column<col?free_column:col;
                else {
                    unsigned int bit=1u<<(owner%32);
                    unsigned int old=distinct_or(reached+owner/32,bit);
                    if(!(old&bit))distinct_or(next+owner/32,bit);
                }
            }
            free_column=distinct_min(free_column);
            distinct_sync();
            if(free_column!=DISTINCT_INT_MAX) {
                if(lane==0) {
                    int col=free_column;
                    while(col>=0) {
                        int row=parents[col],old=row_matches[row];
                        row_matches[row]=col;owners[col]=row;col=old;
                    }
                    *matched=root+1;
                }
                distinct_sync();augmented=true;break;
            }
            bool pending=false;
            for(int word=lane;word<words;word+=DISTINCT_WIDTH)pending|=next[word]!=0u;
            if(!distinct_any(pending))return 4;
            for(int word=lane;word<words;word+=DISTINCT_WIDTH) {
                frontier[word]=next[word];next[word]=0u;
            }
            distinct_sync();
        }
        if(!augmented)return -1;
    }
    return 0;
}

#ifndef RESIDENT_DISTINCT_HOST_TEST
// Compact only candidates permitted by the reviewed whole-cell range and land
// tests. A finite target uncertainty is never removed by a nominal blocked ray.
extern "C" __global__ void gather_distinct_chart_objects(const double* cells,int count,
    const double* mb,const int* mc,const double* me,const int* mo,
    const double* ob,const int* oc,const double* objects,int on,int object_count,
    const double* bearings,const double* tolerances,int reports,
    const double* rects,int nr,double radius,double range_error,double bearing_error,
    double scale,double azimuth,double max_uncertainty,int marker_proofs,int capacity,
    int* object_ids,int* counts,int* flags,int* base_states) {
    int i=blockIdx.x;if(i>=count||threadIdx.x!=0)return;
    counts[i]=0;flags[i]=0;base_states[i]=0;
    const double* c=cells+4*i;Map m={mb,mc,me,mo};
    if(!isfinite(c[0])||!isfinite(c[1])||!isfinite(c[2])||!isfinite(c[3])||
       c[0]>c[2]||c[1]>c[3]) { flags[i]=4;return; }
    bool known=covers_box(m,1,c[0],c[1],c[2],c[3]) &&
        inside(m,2,(c[0]+c[2])*.5,(c[1]+c[3])*.5)==0 &&
        !boundary_box(m,2,c[0],c[1],c[2],c[3]);
    if(known&&covers_box(m,0,c[0],c[1],c[2],c[3])) { base_states[i]=1;return; }
    if(!marker_proofs||reports==0) { flags[i]=1;return; }
    if(reports>256) { flags[i]=2;return; }
    for(int row=0;row<reports;++row)
        if(!isfinite(bearings[row])||bearings[row]<0||bearings[row]>=360||
           !isfinite(tolerances[row])||tolerances[row]<0||tolerances[row]>=180) {
            flags[i]=4;return;
        }
    if(!isfinite(scale)||scale<1||!isfinite(max_uncertainty)||max_uncertainty<0||
       !isfinite(radius)||radius<=0||!isfinite(range_error)||range_error<=0||
       !isfinite(bearing_error)||bearing_error<=0) { flags[i]=4;return; }
    double margin=(radius+range_error)*scale+EPS;
    if(!covers_box(m,3,c[0]-margin,c[1]-margin,c[2]+margin,c[3]+margin)) {
        flags[i]=1;return;
    }
    int stack[64],sp=0,n=0;if(on)stack[sp++]=0;
    double r=margin+max_uncertainty*scale;
    if(!isfinite(r)) { flags[i]=4;return; }
    while(sp) {
        int node=stack[--sp];
        if(node<0||node>=on) { flags[i]=4;return; }
        if(!overlap(ob+4*node,c[0]-r,c[1]-r,c[2]+r,c[3]+r))continue;
        int left=oc[2*node],right=oc[2*node+1];
        if(left>=0) {
            if(sp>61) { flags[i]=4;return; }
            stack[sp++]=left;stack[sp++]=right;continue;
        }
        int id=-left-1;
        if(id<0||id>=object_count) { flags[i]=4;return; }
        const double* object=objects+8*id;
        if(object[6]==2)continue;
        if(!isfinite(object[4])||object[4]<0||!isfinite(object[5])||
           object[5]<0||object[5]>180) { flags[i]=1;return; }
        double uncertainty=object[4]*scale;
        double dx=fmax(fmax(c[0]-object[0],0.),object[0]-c[2]);
        double dy=fmax(fmax(c[1]-object[1],0.),object[1]-c[3]);
        if(hypot(dx,dy)-uncertainty>margin)continue;
        if(uncertainty==0&&barrier(rects,nr,c,object[0],object[1]))continue;
        if(n==capacity) { flags[i]=2;return; }
        object_ids[i*capacity+n++]=id;
    }
    counts[i]=n;
}

// Geodesic envelopes and report edges are evaluated in parallel across objects.
extern "C" __global__ void build_distinct_chart_incidence(const double* cells,int count,
    const double* objects,const int* object_ids,const int* counts,int* flags,
    const int* base_states,const double* bearings,const double* tolerances,int reports,
    double radius,double range_error,double bearing_error,double scale,double azimuth,
    int capacity,int words,unsigned int* incidence) {
    int i=blockIdx.x;if(i>=count||flags[i]||base_states[i]||reports==0||reports>256)return;
    const double* c=cells+4*i;
    double cx=(c[0]+c[2])*.5,cy=(c[1]+c[3])*.5;
    double cr=hypot(c[2]-c[0],c[3]-c[1])*.5;
    for(int col=threadIdx.x;col<counts[i];col+=blockDim.x) {
        const double* object=objects+8*object_ids[i*capacity+col];
        double angle=0.,expansion=180.,lower=0.;bool in_range=true;
#if DISTINCT_TRUE_BEARINGS
        if(!true_bearing_envelope(c,object,range_error,bearing_error,&angle,&expansion,&lower)) {
            atomicOr(flags+i,4);continue;
        }
        in_range=lower<=radius+range_error;
#else
        double distance=hypot(object[0]-cx,object[1]-cy),r=cr+object[4]*scale;
        angle=fmod(atan2(object[0]-cx,object[1]-cy)*DEG+360.,360.);
        expansion=(distance<=r?180.:asin(fmin(1.,r/distance))*DEG)+azimuth;
#endif
        for(int word=0;word<words;++word) {
            unsigned int edges=0u;
            for(int bit=0;bit<32&&word*32+bit<reports;++bit) {
                int row=word*32+bit;
                if(in_range&&distinct_physical_edge(angle,expansion,object[5],
                    bearings[row],tolerances[row],bearing_error))edges|=1u<<bit;
            }
            incidence[((long long)i*capacity+col)*words+word]=edges;
        }
    }
}

// Public graph callers cannot cause out-of-bounds reads with invalid counts.
extern "C" __global__ void validate_distinct_chart_counts(const int* counts,int batch,
    int capacity,int* flags) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if(i<batch&&(counts[i]<0||counts[i]>capacity))flags[i]|=4;
}

extern "C" __global__ void match_distinct_chart_incidence(const unsigned int* incidence,
    const int* counts,const int* input_flags,const int* base_states,int batch,int reports,
    int capacity,int words,int distinct_proofs,int* owners,int* parents,int* row_matches,
    unsigned int* frontiers,int* feasible,int* matched,int* reasons,int* output_flags) {
    int i=blockIdx.x,lane=threadIdx.x;if(i>=batch)return;
    if(lane==0) { feasible[i]=-1;matched[i]=0;reasons[i]=0;output_flags[i]=input_flags[i]; }
    distinct_sync();
    if(input_flags[i]||base_states[i])return;
    unsigned int* reached=frontiers+(long long)i*3*words;
    int reason=distinct_match_graph(incidence+(long long)i*capacity*words,reports,counts[i],words,
        distinct_proofs,lane,owners+(long long)i*capacity,parents+(long long)i*capacity,
        row_matches+(long long)i*reports,reached,reached+words,reached+2*words,matched+i);
    if(lane==0) {
        if(reason<0)output_flags[i]|=1;
        else { reasons[i]=reason;feasible[i]=reason==0?1:0; }
    }
}
#endif
