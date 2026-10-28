// Appended to the unchanged resident CUDA helper source.
// BVH gathering is serial per candidate; geodesic and LOS work is per pair.
extern "C" __global__ void pp_context(
    const double* cells,int count,const double* mb,const int* mc,
    const double* me,const int* mo,const double* ob,const int* oc,int on,
    double radius,double scale,double* context,int* raw_counts,int* flags,int* points) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=count)return;
    Map m={mb,mc,me,mo};const double* c=cells+4*i;
    double x=(c[0]+c[2])*.5,y=(c[1]+c[3])*.5;
    context[4*i]=x;context[4*i+1]=y;raw_counts[i]=0;flags[i]=0;
    points[i]=point(m,x,y);if(points[i]==1)return;
    if(points[i]==2)flags[i]|=1;
    double lon,lat;
    if(!inverse_utm(x,y,&lon,&lat)){flags[i]|=4;return;}
    context[4*i+2]=lon;context[4*i+3]=lat;
    int stack[64],sp=0,n=0;if(on)stack[sp++]=0;
    double r=radius*scale+EPS;
    while(sp){int node=stack[--sp];if(!overlap(ob+4*node,x-r,y-r,x+r,y+r))continue;
        int l=oc[2*node],rr=oc[2*node+1];
        if(l>=0){if(sp>61){flags[i]|=4;return;}stack[sp++]=l;stack[sp++]=rr;continue;}
        ++n;
    }
    raw_counts[i]=n;
}
extern "C" __global__ void pp_gather(
    const double* context,const int* raw_counts,const long long* offsets,int count,
    const double* ob,const int* oc,int on,double radius,double scale,
    int* pair_objects,int* pair_owners) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=count||raw_counts[i]==0)return;
    double x=context[4*i],y=context[4*i+1],r=radius*scale+EPS;
    int stack[64],sp=0,n=0;if(on)stack[sp++]=0;
    while(sp){int node=stack[--sp];if(!overlap(ob+4*node,x-r,y-r,x+r,y+r))continue;
        int l=oc[2*node],rr=oc[2*node+1];
        if(l>=0){stack[sp++]=l;stack[sp++]=rr;continue;}
        long long p=offsets[i]+n++;pair_objects[p]=-l-1;pair_owners[p]=i;
    }
}
extern "C" __global__ void pp_pairs(
    const double* context,const int* owners,const int* object_ids,long long pairs,
    const double* objects,const double* mb,const int* mc,const double* me,const int* mo,
    double radius,double range_error,double* angles,double* ranges,int* exact,
    unsigned char* accepted,int* pair_flags) {
    long long p=(long long)blockIdx.x*blockDim.x+threadIdx.x;if(p>=pairs)return;
    int i=owners[p];const double* c=context+4*i;const double* o=objects+8*object_ids[p];
    accepted[p]=0;pair_flags[p]=0;exact[p]=0;
    double a,d;
    if(!geodesic(c[2],c[3],o[2]/DEG,o[3]/DEG,&a,&d)){pair_flags[p]=4;return;}
    if(d<=2*range_error)pair_flags[p]|=8;
    if(d>radius+range_error)return;
    if(fabs(d-radius)<=range_error)pair_flags[p]|=8;
    Map m={mb,mc,me,mo};int vis=ray(m,c[0],c[1],o[0],o[1]);if(vis==1)return;
    if(vis==2)pair_flags[p]|=1;
    angles[p]=a;ranges[p]=d;exact[p]=(vis==0&&o[4]==0&&o[7]==1);accepted[p]=1;
}
extern "C" __global__ void pp_compact(
    const long long* offsets,int count,int capacity,const int* object_ids,
    const double* pair_angles,const double* pair_ranges,const int* pair_exact,
    const unsigned char* accepted,const int* pair_flags,
    double* angles,double* ranges,int* ids,int* exact,int* visible_counts,int* flags) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;if(i>=count)return;
    int n=0;visible_counts[i]=0;long long base=(long long)i*capacity;
    for(long long p=offsets[i];p<offsets[i+1];++p){
        flags[i]|=pair_flags[p];if(!accepted[p])continue;
        if(n==capacity){flags[i]|=2;return;}
        angles[base+n]=pair_angles[p];ranges[base+n]=pair_ranges[p];
        ids[base+n]=object_ids[p];exact[base+n]=pair_exact[p];++n;
    }
    visible_counts[i]=n;
}
__device__ void pp_atomic_min(double* address,double value){
    unsigned long long* slot=(unsigned long long*)address;
    unsigned long long old=*slot,assumed;
    do{assumed=old;double present=__longlong_as_double(assumed);
       if(present<=value)return;
       old=atomicCAS(slot,assumed,__double_as_longlong(value));
    }while(assumed!=old);
}
__device__ void pp_atomic_max(double* address,double value){
    unsigned long long* slot=(unsigned long long*)address;
    unsigned long long old=*slot,assumed;
    do{assumed=old;double present=__longlong_as_double(assumed);
       if(present>=value)return;
       old=atomicCAS(slot,assumed,__double_as_longlong(value));
    }while(assumed!=old);
}
extern "C" __global__ void pp_group_min(
    const double* ranges,const int* exact,const int* groups,const int* counts,
    int batch,int capacity,double* minimum,int* certified){
    long long p=(long long)blockIdx.x*blockDim.x+threadIdx.x;
    if(p>=(long long)batch*capacity)return;
    int i=p/capacity,j=p%capacity;if(j>=counts[i])return;
    long long key=(long long)i*capacity+groups[p];
    pp_atomic_min(minimum+key,ranges[p]);atomicMin(certified+key,exact[p]);
}
extern "C" __global__ void pp_group_rep(
    const double* ranges,const int* ids,const int* groups,const int* counts,
    int batch,int capacity,const double* minimum,int* representative){
    long long p=(long long)blockIdx.x*blockDim.x+threadIdx.x;
    if(p>=(long long)batch*capacity)return;
    int i=p/capacity,j=p%capacity;if(j>=counts[i])return;
    long long key=(long long)i*capacity+groups[p];
    if(ranges[p]==minimum[key])atomicMin(representative+key,ids[p]);
}
extern "C" __global__ void pp_group_angles(
    const double* angles,const double* ranges,const int* ids,const int* groups,
    const int* counts,int batch,int capacity,const double* minimum,
    const int* representative,double* output){
    long long p=(long long)blockIdx.x*blockDim.x+threadIdx.x;
    if(p>=(long long)batch*capacity)return;
    int i=p/capacity,j=p%capacity;if(j>=counts[i])return;
    long long key=(long long)i*capacity+groups[p];
    if(ranges[p]==minimum[key]&&ids[p]==representative[key])output[key]=angles[p];
}
extern "C" __global__ void pp_group_extent(
    const double* angles,const double* ranges,const int* ids,const int* groups,
    const int* counts,int batch,int capacity,const double* minimum,
    const double* representative_angles,const double* objects,double range_error,
    double* extent,int* ties){
    long long p=(long long)blockIdx.x*blockDim.x+threadIdx.x;
    if(p>=(long long)batch*capacity)return;
    int i=p/capacity,j=p%capacity;if(j>=counts[i])return;
    long long key=(long long)i*capacity+groups[p];
    pp_atomic_max(extent+key,angular(angles[p],representative_angles[key])+objects[8*ids[p]+5]);
    if(fabs(ranges[p]-minimum[key])<=2*range_error)atomicAdd(ties+key,1);
}
