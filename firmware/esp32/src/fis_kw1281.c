/* Adapted from muki01/VAG_KW1281, commit
 * 1860b11a4c7052795dad0280ad54510c17252606,
 * VAG_KW1281/KW1281.ino (writeBlock/readBlock, initOBD2/getPID), GPL-3.0.
 * Bounded buffers, validated complements/counters, absolute deadlines, and
 * read-only operations replace the upstream unbounded Arduino routines.
 * License: ../LICENSES/GPL-3.0.txt. See docs/THIRD_PARTY_LICENSES.md. */
#include "fis_kw1281.h"
#include <string.h>
#define TRY(x) do { fis_result r_=(x); if(r_!=FIS_OK) return r_; } while(0)
#define ACK 0x09u
#define NAK 0x0au
typedef struct { uint8_t group_id; fis_catalog_status status; uint32_t metadata_candidate_count; } fis_catalog_entry;
#include "fis_group_catalog.inc"
static bool physically_allowed_group(uint8_t group);
static fis_result check(fis_session *s,uint64_t d) {
 if(!s || !s->io.now_ms || !s->io.cancelled || !s->io.slow_init || !s->io.wait_ms || !s->io.read_byte || !s->io.write_byte) return FIS_INVALID;
 if(s->io.cancelled(s->io.ctx)) return FIS_CANCELLED;
 return s->io.now_ms(s->io.ctx)>=d ? FIS_TIMEOUT : FIS_OK;
}
static fis_result read_b(fis_session *s,uint8_t *v,uint64_t d) { TRY(check(s,d)); TRY(s->io.read_byte(s->io.ctx,v,d)); return check(s,d); }
static fis_result write_b(fis_session *s,uint8_t v,uint64_t d) { TRY(check(s,d)); TRY(s->io.write_byte(s->io.ctx,v,d)); return check(s,d); }
static fis_result receive(fis_session *s,uint8_t *b,uint8_t *length,uint64_t d) {
 uint8_t len; unsigned i;
 TRY(read_b(s,&len,d)); if(len<3) return FIS_PROTOCOL;
 TRY(write_b(s,(uint8_t)~len,d));
 for(i=0;i<len;i++) { TRY(read_b(s,&b[i],d)); if(i+1u<len) TRY(write_b(s,(uint8_t)~b[i],d)); }
 if((s->counter_set && b[0]!=s->counter) || b[len-1u]!=3) return FIS_PROTOCOL;
 s->counter=(uint8_t)(b[0]+1u); s->counter_set=true; *length=len; return FIS_OK;
}
static fis_result send(fis_session *s,uint8_t title,int data,uint64_t d) {
 uint8_t b[5], reply; unsigned length=data<0?2u:3u, n=length+2u,i;
 if(!s->counter_set) return FIS_PROTOCOL;
 b[0]=(uint8_t)(length+1u); b[1]=s->counter; b[2]=title; if(data>=0) b[3]=(uint8_t)data; b[n-1u]=3;
 for(i=0;i<n;i++) { TRY(write_b(s,b[i],d)); if(i+1u<n) { TRY(read_b(s,&reply,d)); if(reply!=(uint8_t)(b[i]^0xffu)) return FIS_PROTOCOL; } }
 s->counter=(uint8_t)(s->counter+1u); return FIS_OK;
}
/* A complete response has at most 32 blocks; each nonterminal block is ACKed. */
static fis_result response(fis_session *s, fis_identity *id, fis_group *group, bool *more, uint64_t d) {
 uint8_t b[255], len; unsigned i, count; bool got=false;
 for(count=0;count<32;count++) {
  TRY(receive(s,b,&len,d));
  if(b[1]==NAK) return FIS_REJECTED;
  if(b[1]==ACK) return (id || group) && !got ? FIS_PROTOCOL : FIS_OK;
  if(id && b[1]==0xf6 && len>3) {
   if(id->len && id->len<FIS_IDENTITY_CAP) id->bytes[id->len++]='\n';
   if((size_t)(len-3u)>FIS_IDENTITY_CAP-id->len) return FIS_LIMIT;
   if(b[2]&0x80u) *more=true;
   for(i=2;i<(unsigned)len-1u;i++) id->bytes[id->len++]=(uint8_t)(b[i]&0x7fu);
   got=true;
  } else if(group && b[1]==0xe7 && len>3 && !got) {
   group->len=(size_t)len-3u; memcpy(group->bytes,&b[2],group->len); got=true;
  } else if(!id && !group) return FIS_PROTOCOL;
  TRY(send(s,ACK,-1,d));
 }
 return FIS_LIMIT;
}
static fis_result identify_inner(fis_session *s,fis_identity *id,uint64_t d) {
 uint8_t a,b,c; unsigned segment; bool more=false;
 TRY(check(s,d)); TRY(s->io.slow_init(s->io.ctx,1,d)); TRY(check(s,d));
 TRY(read_b(s,&a,d)); TRY(read_b(s,&b,d)); TRY(read_b(s,&c,d));
 if(a!=0x55) return FIS_PROTOCOL;
 TRY(s->io.wait_ms(s->io.ctx,25,d)); TRY(write_b(s,(uint8_t)~c,d));
 if((((unsigned)c&127u)<<7 | ((unsigned)b&127u))!=1281u) return FIS_PROTOCOL;
 for(segment=0;segment<16;segment++) {
  more=false; TRY(response(s,id,NULL,&more,d));
  if(!more) {
   size_t j; bool nonblank=false;
   for(j=0;j<id->len && id->bytes[j]!='\n';j++)
    if(id->bytes[j]!=' ' && id->bytes[j]!='\0' && id->bytes[j]!='\r') nonblank=true;
   if(!nonblank) return FIS_PROTOCOL;
   s->identified=true; return FIS_OK;
  }
  if(segment==15) return FIS_LIMIT;
  TRY(send(s,0,-1,d));
 }
 return FIS_LIMIT;
}
fis_result fis_identify(fis_session *s,fis_identity *id,uint64_t d) {
 fis_result r; if(id) id->len=0;
 if(!s || !id) return FIS_INVALID;
 s->identified=false; s->counter_set=false;
 r=identify_inner(s,id,d); if(r!=FIS_OK) { s->identified=false; s->counter_set=false; id->len=0; } return r;
}
fis_result fis_keep_alive(fis_session *s,uint64_t d) {
 fis_result r; bool unused=false;
 if(!s || !s->identified) return FIS_INVALID;
 r=send(s,ACK,-1,d); if(r==FIS_OK) r=response(s,NULL,NULL,&unused,d);
 if(r!=FIS_OK) { s->identified=false; s->counter_set=false; } return r;
}
fis_result fis_read_group(fis_session *s,uint8_t group,fis_group *out,uint64_t d) {
 fis_result r; bool unused=false;
 if(out) out->len=0;
 if(!s || !out || !s->identified || !physically_allowed_group(group)) return FIS_INVALID;
 r=send(s,0x29,group,d); if(r==FIS_OK) r=response(s,NULL,out,&unused,d);
 if(r!=FIS_OK) { out->len=0; s->identified=false; s->counter_set=false; } return r;
}

/* Physical authorization is an explicit protocol gate, independent of labels. */
static bool physically_allowed_group(uint8_t group) {
 return group==1u || group==3u || group==11u;
}

fis_result fis_catalog_lookup(uint8_t group,fis_group_metadata *out) {
 size_t i;
 if(!out) return FIS_INVALID;
 out->group_id=group;
 out->status=FIS_CATALOG_UNKNOWN;
 out->physical_read_allowed=false;
 out->name=NULL;
 out->source="data/sensor_catalog.v1.json";
 out->metadata_candidate_count=0u;
 for(i=0;i<sizeof(FIS_GROUP_CATALOG)/sizeof(FIS_GROUP_CATALOG[0]);i++) {
  if(FIS_GROUP_CATALOG[i].group_id!=group) continue;
  out->status=FIS_GROUP_CATALOG[i].status;
  out->metadata_candidate_count=FIS_GROUP_CATALOG[i].metadata_candidate_count;
  if(group==1u) out->name="Motor";
  else if(group==3u) out->name="Massa de ar";
  else if(group==11u) out->name="Turbo";
  else if(out->status==FIS_CATALOG_METADATA_ONLY) out->name="Metadados de labels; ECU por confirmar";
  break;
 }
 out->physical_read_allowed=physically_allowed_group(group);
 return FIS_OK;
}

void fis_monitor_init(fis_monitor *monitor) {
 if(!monitor) return;
 memset(monitor,0,sizeof(*monitor));
 monitor->status=FIS_MONITOR_STOPPED;
}

fis_result fis_monitor_start(
 fis_monitor *monitor,const uint8_t *groups,size_t count,uint32_t min_period_ms) {
 static const uint8_t canonical[]={1u,3u,11u};
 size_t i,j;
 if(!monitor || !groups || count==0u || count>FIS_MONITOR_MAX_GROUPS) return FIS_INVALID;
 if(monitor->status==FIS_MONITOR_RUNNING || monitor->in_flight) return FIS_BUSY;
 if(min_period_ms<FIS_MONITOR_MIN_PERIOD_MS || min_period_ms>FIS_MONITOR_MAX_PERIOD_MS) return FIS_INVALID;
 for(i=0;i<count;i++) {
  if(!physically_allowed_group(groups[i])) return FIS_INVALID;
  for(j=0;j<i;j++) if(groups[j]==groups[i]) return FIS_INVALID;
 }
 fis_monitor_init(monitor);
 monitor->min_period_ms=min_period_ms;
 for(i=0;i<sizeof(canonical)/sizeof(canonical[0]);i++) {
  for(j=0;j<count;j++) {
   if(groups[j]==canonical[i]) {
    monitor->groups[monitor->group_count++]=canonical[i];
    break;
   }
  }
 }
 if(monitor->group_count!=count) {
  fis_monitor_init(monitor);
  return FIS_INVALID;
 }
 monitor->status=FIS_MONITOR_RUNNING;
 return FIS_OK;
}

fis_result fis_monitor_next(fis_monitor *monitor,uint64_t now_ms,uint8_t *group) {
 size_t index;
 if(!monitor || !group) return FIS_INVALID;
 if(monitor->status!=FIS_MONITOR_RUNNING) return FIS_CANCELLED;
 if(monitor->in_flight) return FIS_BUSY;
 if(monitor->group_count==0u || monitor->group_count>FIS_MONITOR_MAX_GROUPS) return FIS_INVALID;
 if(monitor->clock_set && now_ms<monitor->last_observed_ms) return FIS_INVALID;
 monitor->last_observed_ms=now_ms;
 monitor->clock_set=true;
 index=monitor->cursor;
 if(monitor->has_completed[index]) {
  uint64_t completed=monitor->last_completed_ms[index];
  if(now_ms<completed || now_ms-completed<(uint64_t)monitor->min_period_ms) return FIS_WAIT;
 }
 monitor->request_started_ms=now_ms;
 monitor->in_flight_index=index;
 monitor->in_flight=true;
 *group=monitor->groups[index];
 return FIS_OK;
}

fis_result fis_monitor_complete(
 fis_monitor *monitor,uint8_t group,uint64_t completed_ms,fis_result request_result) {
 size_t index;
 if(!monitor) return FIS_INVALID;
 if(!monitor->in_flight) return monitor->status==FIS_MONITOR_RUNNING ? FIS_INVALID : FIS_CANCELLED;
 if(monitor->in_flight_index>=monitor->group_count) return FIS_INVALID;
 index=monitor->in_flight_index;
 if(group!=monitor->groups[index]) return FIS_INVALID;
 if(monitor->status!=FIS_MONITOR_RUNNING) {
  monitor->in_flight=false;
  monitor->group_count=0u;
  monitor->cursor=0u;
  monitor->clock_set=false;
  return FIS_CANCELLED;
 }
 monitor->in_flight=false;
 if(request_result!=FIS_OK) {
  monitor->status=FIS_MONITOR_SUSPENDED;
  return request_result;
 }
 if(completed_ms<monitor->request_started_ms || (monitor->clock_set && completed_ms<monitor->last_observed_ms)) {
  monitor->status=FIS_MONITOR_SUSPENDED;
  return FIS_INVALID;
 }
 monitor->last_completed_ms[index]=completed_ms;
 monitor->has_completed[index]=true;
 monitor->cursor=(index+1u)%monitor->group_count;
 monitor->last_observed_ms=completed_ms;
 monitor->clock_set=true;
 return FIS_OK;
}

void fis_monitor_stop(fis_monitor *monitor) {
 if(!monitor) return;
 monitor->status=FIS_MONITOR_STOPPED;
 if(!monitor->in_flight) {
  monitor->group_count=0u;
  monitor->cursor=0u;
  monitor->clock_set=false;
 }
}
