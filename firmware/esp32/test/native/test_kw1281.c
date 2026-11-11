#include "fis_kw1281.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint8_t rx[4096], tx[4096]; size_t n, p, t; uint64_t now; bool cancel; } fixture;
static uint64_t now(void *p) { return ((fixture *)p)->now; }
static bool cancelled(void *p) { return ((fixture *)p)->cancel; }
static fis_result init(void *p, uint8_t address, uint64_t d) { (void)p; (void)d; assert(address==1); return FIS_OK; }
static fis_result wait_ms(void *p, uint32_t ms, uint64_t d) { fixture *f=p; f->now+=ms; return f->now>=d ? FIS_TIMEOUT : FIS_OK; }
static fis_result rd(void *p, uint8_t *v, uint64_t d) { fixture *f=p; (void)d; if(f->p==f->n) return FIS_TIMEOUT; *v=f->rx[f->p++]; return FIS_OK; }
static fis_result wr(void *p, uint8_t v, uint64_t d) { fixture *f=p; (void)d; assert(f->t<sizeof f->tx); f->tx[f->t++]=v; return FIS_OK; }
static fis_session session(fixture *f) { fis_session s={{f,now,cancelled,init,wait_ms,rd,wr},0,false,false}; return s; }
static void byte(fixture *f,uint8_t v) { assert(f->n<sizeof f->rx); f->rx[f->n++]=v; }
static void block(fixture *f,uint8_t c,uint8_t t,const uint8_t *data,size_t n) { size_t i; byte(f,(uint8_t)(n+3)); byte(f,c); byte(f,t); for(i=0;i<n;i++) byte(f,data[i]); byte(f,3); }
static void ack_send(fixture *f,uint8_t c,uint8_t t,int group) { byte(f,(uint8_t)~(group<0?3:4)); byte(f,(uint8_t)~c); byte(f,(uint8_t)~t); if(group>=0) byte(f,(uint8_t)~group); }
static void startup(fixture *f) { static const uint8_t id[]="038906018AF"; byte(f,0x55); byte(f,1); byte(f,0x8a); block(f,0,0xf6,id,sizeof id-1); ack_send(f,1,9,-1); block(f,2,9,NULL,0); }
static void identify(fis_session *s, fixture *f) {
 static const uint8_t expected[]={
  0x75, /* keyword complement */
  0xf1,0xff,0x09,0xcf,0xcc,0xc7,0xc6,0xcf,0xc9,0xcf,0xce,0xc7,0xbe,0xb9,
  0x03,0x01,0x09,0x03, /* ACK frame, including terminator */
  0xfc,0xfd,0xf6 /* terminal receive-block complements */
 };
 fis_identity id; startup(f); assert(fis_identify(s,&id,1000)==FIS_OK);
 assert(id.len==11 && memcmp(id.bytes,"038906018AF",11)==0);
 assert(f->t==sizeof expected && !memcmp(f->tx,expected,sizeof expected));
}
static void test_groups(void) {
 static const uint8_t expected_group_one[]={
  0x04,0x03,0x29,0x01,0x03, /* group request, including terminator */
  0xf9,0xfb,0x18,0xf3,0xef,0xdf, /* E7 receive-block complements */
  0x03,0x05,0x09,0x03, /* ACK frame, including terminator */
  0xfc,0xf9,0xf6 /* terminal receive-block complements */
 };
 const uint8_t gs[]={1,3,11}; size_t i;
 for(i=0;i<3;i++) {
  fixture f={0}; fis_session s=session(&f); fis_group out; uint8_t raw[]={12,16,32}; size_t before;
  identify(&s,&f); before=f.t;
  ack_send(&f,3,0x29,gs[i]); block(&f,4,0xe7,raw,3); ack_send(&f,5,9,-1); block(&f,6,9,NULL,0);
  assert(fis_read_group(&s,gs[i],&out,1000)==FIS_OK); assert(out.len==3 && !memcmp(out.bytes,raw,3));
  if(gs[i]==1) assert(f.t-before==sizeof expected_group_one && !memcmp(f.tx+before,expected_group_one,sizeof expected_group_one));
  before=f.t;
  assert(fis_read_group(&s,42,&out,1000)==FIS_INVALID);
  assert(out.len==0 && f.t==before && f.p==f.n);
 }
}
static void test_invalid_group_session_clears_output(void) {
 fixture f={0}; fis_session s=session(&f); fis_group out={{12,16,32},3};
 assert(fis_read_group(NULL,1,&out,100)==FIS_INVALID); assert(out.len==0);
 out.len=3;
 assert(fis_read_group(&s,1,&out,100)==FIS_INVALID); assert(out.len==0 && f.t==0);
 assert(fis_read_group(&s,1,NULL,100)==FIS_INVALID);
}
static void test_null_identify_session_clears_output(void) {
 fis_identity id={{'A','F','N'},3};
 assert(fis_identify(NULL,&id,100)==FIS_INVALID); assert(id.len==0);
 assert(fis_identify(NULL,NULL,100)==FIS_INVALID);
}
static void test_bad(void) { fixture f={0}; fis_session s=session(&f); fis_group out; fis_identity id; s.identified=true; assert(fis_read_group(&s,42,&out,100)==FIS_INVALID && f.t==0); s.identified=false; byte(&f,0x55); byte(&f,1); byte(&f,0x8a); byte(&f,2); assert(fis_identify(&s,&id,100)==FIS_PROTOCOL); }
static void test_failures(void) { fixture f={0}; fis_session s=session(&f); fis_identity id; byte(&f,0x55); byte(&f,1); byte(&f,0x8a); block(&f,0,0xf6,(const uint8_t *)"a",1); byte(&f,0); assert(fis_identify(&s,&id,100)==FIS_PROTOCOL); memset(&f,0,sizeof f); s=session(&f); f.now=100; assert(fis_identify(&s,&id,100)==FIS_TIMEOUT && f.t==0); f.now=0; f.cancel=true; assert(fis_identify(&s,&id,100)==FIS_CANCELLED); memset(&f,0,sizeof f); s=session(&f); identify(&s,&f); ack_send(&f,3,9,-1); block(&f,99,9,NULL,0); assert(fis_keep_alive(&s,1000)==FIS_PROTOCOL); }
static void test_limits_and_continuation(void) {
 fixture f={0}; fis_session s=session(&f); fis_identity id; unsigned i; uint8_t long_id[250];
 memset(long_id,'A',sizeof long_id); byte(&f,0x55); byte(&f,1); byte(&f,0x8a);
 for(i=0;i<3;i++) { block(&f,(uint8_t)(i*2),0xf6,long_id,sizeof long_id); if(i<2) ack_send(&f,(uint8_t)(i*2+1),9,-1); }
 assert(fis_identify(&s,&id,1000)==FIS_LIMIT && !s.identified && id.len==0);
 memset(&f,0,sizeof f); s=session(&f); byte(&f,0x55); byte(&f,1); byte(&f,0x8a);
 { uint8_t first[]={0xC1}; block(&f,0,0xf6,first,1); }
 ack_send(&f,1,9,-1); block(&f,2,9,NULL,0); ack_send(&f,3,0,-1);
 block(&f,4,0xf6,(const uint8_t *)"B",1); ack_send(&f,5,9,-1); block(&f,6,9,NULL,0);
 assert(fis_identify(&s,&id,1000)==FIS_OK && id.len==3 && !memcmp(id.bytes,"A\nB",3));
 ack_send(&f,7,9,-1); block(&f,8,9,NULL,0); assert(fis_keep_alive(&s,1000)==FIS_OK);
 memset(&f,0,sizeof f); s=session(&f); byte(&f,0x55); byte(&f,1); byte(&f,0x8a);
 for(i=0;i<32;i++) { block(&f,(uint8_t)(2*i),0xf6,(const uint8_t *)"x",1); ack_send(&f,(uint8_t)(2*i+1),9,-1); }
 assert(fis_identify(&s,&id,1000)==FIS_LIMIT);
}
static void test_catalog_contract(void) {
 fis_group_metadata info; fixture f={0}; fis_session s=session(&f); fis_group out;
 static const uint8_t ids[]={1,3,11};
 static const char *names[]={"Motor","Massa de ar","Turbo"};
 size_t i; unsigned id;
 for(id=0;id<256u;id++) {
  assert(fis_catalog_lookup((uint8_t)id,&info)==FIS_OK);
  assert(info.group_id==(uint8_t)id);
  assert(info.physical_read_allowed==(id==1u || id==3u || id==11u));
  if(id==1u || id==3u || id==11u) assert(info.status==FIS_CATALOG_ACTIVE_ALLOWED);
  else if(info.metadata_candidate_count>0u) assert(info.status==FIS_CATALOG_METADATA_ONLY);
  else assert(info.status==FIS_CATALOG_UNKNOWN);
 }
 for(i=0;i<3;i++) {
  assert(fis_catalog_lookup(ids[i],&info)==FIS_OK);
  assert(info.group_id==ids[i]);
  assert(info.status==FIS_CATALOG_ACTIVE_ALLOWED && info.physical_read_allowed);
  assert(strcmp(info.name,names[i])==0 && info.source!=NULL);
 }
 assert(fis_catalog_lookup(2,&info)==FIS_OK);
 assert(info.status==FIS_CATALOG_METADATA_ONLY && !info.physical_read_allowed);
 assert(info.metadata_candidate_count>0 && info.name!=NULL && info.source!=NULL);
 assert(fis_catalog_lookup(255,&info)==FIS_OK);
 assert(info.status==FIS_CATALOG_UNKNOWN && !info.physical_read_allowed);
 assert(info.metadata_candidate_count==0u && info.name==NULL && info.source!=NULL);
 s.identified=true;
 assert(fis_read_group(&s,2,&out,100)==FIS_INVALID && f.t==0);
}
static void test_monitor_is_opt_in_and_sequential(void) {
 fis_monitor monitor={0}; uint8_t selected[]={11,3,1}, group=0;
 fis_monitor_init(&monitor);
 assert(monitor.status==FIS_MONITOR_STOPPED);
 assert(fis_monitor_next(&monitor,0,&group)==FIS_CANCELLED);
 assert(fis_monitor_start(&monitor,selected,3,1000)==FIS_OK);
 assert(monitor.status==FIS_MONITOR_RUNNING && monitor.group_count==3);
 assert(monitor.groups[0]==1 && monitor.groups[1]==3 && monitor.groups[2]==11);
 assert(fis_monitor_next(&monitor,0,&group)==FIS_OK && group==1);
 assert(fis_monitor_next(&monitor,0,&group)==FIS_BUSY); /* one request at a time */
 assert(fis_monitor_complete(&monitor,1,100,FIS_OK)==FIS_OK);
 assert(fis_monitor_next(&monitor,100,&group)==FIS_OK && group==3);
 assert(fis_monitor_complete(&monitor,3,120,FIS_OK)==FIS_OK);
 assert(fis_monitor_next(&monitor,120,&group)==FIS_OK && group==11);
 assert(fis_monitor_complete(&monitor,11,160,FIS_OK)==FIS_OK);
 assert(fis_monitor_next(&monitor,160,&group)==FIS_WAIT);
 assert(fis_monitor_next(&monitor,1099,&group)==FIS_WAIT);
 assert(fis_monitor_next(&monitor,1100,&group)==FIS_OK && group==1);
}
static void test_monitor_bounds_allowlist_cancel_and_error_suspend(void) {
 fis_monitor monitor={0}; uint8_t selected[]={1,11}, group=0; uint8_t bad[]={1,2}; uint8_t duplicate[]={1,1};
 fis_monitor_init(&monitor);
 assert(fis_monitor_start(&monitor,NULL,1,1000)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,selected,0,1000)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,selected,2,499)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,selected,2,60001)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,bad,2,1000)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,duplicate,2,1000)==FIS_INVALID);
 assert(fis_monitor_start(&monitor,selected,2,1000)==FIS_OK);
 assert(fis_monitor_start(&monitor,selected,2,1000)==FIS_BUSY);
 assert(fis_monitor_next(&monitor,0,&group)==FIS_OK && group==1);
 assert(fis_monitor_complete(&monitor,11,20,FIS_OK)==FIS_INVALID);
 fis_monitor_stop(&monitor);
 assert(monitor.status==FIS_MONITOR_STOPPED && fis_monitor_next(&monitor,20,&group)==FIS_CANCELLED);
 assert(monitor.in_flight);
 assert(fis_monitor_start(&monitor,selected,2,1000)==FIS_BUSY);
 assert(fis_monitor_complete(&monitor,1,30,FIS_CANCELLED)==FIS_CANCELLED);
 assert(!monitor.in_flight);
 assert(fis_monitor_start(&monitor,selected,2,1000)==FIS_OK);
 assert(fis_monitor_next(&monitor,20,&group)==FIS_OK && group==1);
 assert(fis_monitor_complete(&monitor,1,30,FIS_PROTOCOL)==FIS_PROTOCOL);
 assert(monitor.status==FIS_MONITOR_SUSPENDED && fis_monitor_next(&monitor,31,&group)==FIS_CANCELLED);
}
int main(void) { test_groups(); test_invalid_group_session_clears_output(); test_null_identify_session_clears_output(); test_bad(); test_failures(); test_limits_and_continuation(); test_catalog_contract(); test_monitor_is_opt_in_and_sequential(); test_monitor_bounds_allowlist_cancel_and_error_suspend(); puts("native KW1281 tests passed"); return 0; }
