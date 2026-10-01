// Source-only acceptance checker for examples/smoke; uses raw ETW payloads,
// not locale-dependent manifest rendering. Build with cl /EHsc /std:c++17
// /Fe:<temporary-directory>/capture_check.exe capture_check.cpp advapi32.lib.
#include <windows.h>
#include <evntrace.h>
#include <evntcons.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <stdexcept>
#include <map>
#include <set>
#include <cstdint>

static void require(bool c, const char* m) { if(!c) throw std::runtime_error(m); }
struct Reader {
    const std::vector<unsigned char>& b; size_t at=0;
    uint64_t number(size_t n) {
        require(n<=b.size()-at,"Truncated payload");
        uint64_t v=0; for(size_t i=0;i<n;++i) v|=uint64_t(b[at++])<<(8*i); return v;
    }
    std::string text(bool wide,bool counted) {
        size_t units=counted?size_t(number(2)):0;
        if(!counted) {
            size_t p=at;
            for(;;) {
                require(p+(wide?2:1)<=b.size(),"Missing string terminator");
                if(!b[p] && (!wide || !b[p+1])) break;
                ++units; p+=wide?2:1;
            }
        }
        size_t size=units*(wide?2:1); require(size<=b.size()-at,"Truncated string");
        std::string result;
        if(wide && units) {
            std::wstring w;
            for(size_t i=0;i<units;++i) w.push_back(wchar_t(b[at+i*2]|(unsigned(b[at+i*2+1])<<8)));
            int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,w.data(),int(w.size()),nullptr,0,nullptr,nullptr);
            require(n>0,"Invalid UTF16"); result.resize(n);
            require(WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,w.data(),int(w.size()),&result[0],n,nullptr,nullptr)==n,"UTF16 conversion failed");
        } else if(!wide && size) {
            result.assign(reinterpret_cast<const char*>(b.data()+at),size);
            require(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,result.data(),int(size),nullptr,0)>0,"Invalid UTF8");
        }
        at+=size; if(!counted) at+=wide?2:1; return result;
    }
    void done() { require(at==b.size(),"Trailing payload bytes"); }
};

struct Record { DWORD pid, tid; USHORT id; bool info; LONGLONG time; std::vector<unsigned char> bytes; };
static std::vector<Record> records;
static bool callback_failed = false;
static const GUID events = {0xad62181d,0x781e,0x4f0e,{0xbf,0x1b,0x32,0x13,0x29,0x28,0xcf,0x4c}};
static const GUID info = {0xa07dc94f,0x631f,0x41a1,{0x94,0xca,0x5a,0xfe,0x40,0xa0,0x97,0x78}};
static const GUID thread_provider = {0x3d6fa8d1,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static void WINAPI collect(EVENT_RECORD* e) {
    bool thread_name = e->EventHeader.ProviderId == thread_provider && e->EventHeader.EventDescriptor.Opcode == 72;
    if (e->EventHeader.ProviderId != events && e->EventHeader.ProviderId != info && !thread_name) return;
    try {
        Record r{e->EventHeader.ProcessId,e->EventHeader.ThreadId,e->EventHeader.EventDescriptor.Id,
                 e->EventHeader.ProviderId == info,e->EventHeader.TimeStamp.QuadPart,{}};
        auto p = static_cast<unsigned char*>(e->UserData);
        if (e->UserDataLength) r.bytes.assign(p,p+e->UserDataLength);
        if (thread_name) {
            r.info = true;
            r.id = 1000;
        }
        records.push_back(std::move(r));
    } catch (...) { callback_failed = true; }
}

struct Event { std::string name,data; uint32_t color; bool wide; };
static Event decode(const Record& r) {
    require(r.id>=100 && r.id<=106 && r.id!=101,"Unknown instrumentation event");
    Reader p{r.bytes}; bool wide=r.id==102 || r.id==104 || r.id==106;
    bool counted=r.id==105 || r.id==106;
    Event e{p.text(wide,counted),p.text(wide,counted),0xffffffff,wide};
    if(r.id>=103) e.color=uint32_t(p.number(4)); p.done(); return e;
}
static void verify() {
    // Select one smoke workload from the system-wide trace, not background apps.
    std::set<DWORD> candidates;
    for(const auto& r:records)
        if(!r.info && r.id!=101 && decode(r).name=="Odin Frame") candidates.insert(r.pid);
    require(candidates.size()==1,"Expected one smoke process");
    DWORD pid=*candidates.begin(),main_tid=0,worker_tid=0;
    std::map<DWORD,std::string> names;
    std::map<std::string,int> counts;
    struct Scope { Event event; LONGLONG start; int children=0; };
    std::map<std::pair<DWORD,uint64_t>,std::vector<Scope>> stacks;
    std::map<DWORD,uint64_t> active;
    std::vector<std::pair<USHORT,std::vector<uint64_t>>> fibers;
    uint64_t parent=0,child=0; int ends=0,raw_names=0;
    for(const auto& r:records) {
        Reader p{r.bytes};
        // Kernel Thread/SetName uses payload PID/TID, not header PID/TID.
        if(r.id==1000) {
            DWORD process=DWORD(p.number(4)),thread=DWORD(p.number(4));
            if(process!=pid) continue;
            auto name=p.text(true,false); p.done(); names[thread]=name;
            if(name=="Raw Thread") ++raw_names;
            continue;
        }
        if(r.pid!=pid) continue;
        if(r.info) {
            if(r.id==103) continue;
            if(r.id==102 || r.id==104) {
                auto name=p.text(false,r.id==104); DWORD tid=DWORD(p.number(4));
                p.done(); names[tid]=name; if(name=="Raw Thread") ++raw_names; continue;
            }
            require(r.id>=105 && r.id<=108,"Unknown fiber event");
            require(worker_tid && r.tid==worker_tid,"Wrong fiber thread");
            std::vector<uint64_t> ids{p.number(8)};
            if(r.id==107) ids.push_back(p.number(8));
            p.done(); fibers.push_back({r.id,ids});
            if(fibers.size()==1) { parent=ids[0]; require(r.id==105 && parent,"Missing parent registration"); }
            if(r.id==107 && !child) child=ids[1];
            if(r.id==108) active[r.tid]=ids[0];
            continue;
        }
        auto& stack=stacks[{r.tid,active[r.tid]}];
        if(r.id==101) {
            p.done(); require(!stack.empty(),"Unmatched end");
            auto scope=stack.back(); stack.pop_back(); require(r.time>scope.start,"Invalid interval");
            if(scope.event.name=="Odin Frame") require(scope.children==1,"Frame child count");
            if(scope.event.name=="Odin Child") {
                require(!stack.empty() && stack.back().event.name=="Odin Frame","Child not nested");
                require(scope.start>=stack.back().start,"Child begins before parent"); ++stack.back().children;
            }
            ++ends; continue;
        }
        Event e=decode(r); int index=counts[e.name]++;
        bool worker=e.name=="Odin Worker Event" || e.name=="Odin Fiber Event";
        require(names[r.tid]==(worker?"Odin Worker":"Odin Main"),"Wrong event thread name");
        if(worker) { if(!worker_tid) worker_tid=r.tid; require(worker_tid==r.tid,"Worker thread changed"); }
        else { if(!main_tid) main_tid=r.tid; require(main_tid==r.tid,"Main thread changed"); }
        require(e.color==(e.name=="Odin Frame"?0x123456ff:0xffffffff),"Wrong color");
        std::string data; bool wide=false;
        if(e.name=="Odin Frame") data="frame="+std::to_string(index);
        else if(e.name=="Odin UTF8 caf\xc3\xa9") data="caf\xc3\xa9";
        else if(e.name=="Odin Wide") { data="note=\xf0\x9f\x8e\xb5"; wide=true; }
        else if(e.name=="Raw UTF8 Z" || e.name=="Raw UTF8 N") data="raw";
        else if(e.name=="Raw UTF16 Z" || e.name=="Raw UTF16 N") { data="raw"; wide=true; }
        else require(e.name=="Odin Child" || worker,"Unexpected event or suffix");
        require(e.data==data,"Wrong data, Unicode, or substring length");
        require(e.wide==wide,"Wrong payload encoding");
        if(e.name=="Odin Fiber Event") require(child && active[r.tid]==child,"Wrong child fiber attribution");
        else require(active[r.tid]==0,"Unexpected fiber attribution");
        if(e.name!="Odin Child") require(stack.empty(),"Unexpected nesting");
        stack.push_back({e,r.time});
    }
    const std::map<std::string,int> expected{
        {"Odin Frame",3},{"Odin Child",3},{"Odin UTF8 caf\xc3\xa9",1},{"Odin Wide",1},
        {"Raw UTF8 Z",1},{"Raw UTF8 N",1},{"Raw UTF16 Z",1},{"Raw UTF16 N",1},
        {"Odin Worker Event",1},{"Odin Fiber Event",1}};
    require(counts==expected && ends==14,"Wrong exact event counts");
    for(const auto& item:stacks) require(item.second.empty(),"Unmatched begin");
    require(main_tid && worker_tid && main_tid!=worker_tid && raw_names==1,"Wrong naming or thread separation");
    require(names[main_tid]=="Odin Main" && names[worker_tid]=="Odin Worker","Wrong final names");
    require(parent && child && parent!=child,"Invalid fiber IDs");
    const std::vector<std::pair<USHORT,std::vector<uint64_t>>> expected_fibers{
        {105,{parent}},{107,{parent,child}},{105,{child}},{108,{child}},
        {106,{child}},{107,{child,parent}},{108,{parent}},{106,{parent}}};
    require(fibers==expected_fibers,"Wrong fiber lifecycle");
    std::printf("Capture passed: pid=%lu main=%lu worker=%lu; 14 complete events, 3 nested children; UTF8=c3a9 UTF16=d83c,dfb5; color=123456ff; 2 fibers, 8 lifecycle records; exact names/data/counts verified\n",pid,main_tid,worker_tid);
}
int wmain(int argc, wchar_t** argv) {
    if (argc != 2) { std::fprintf(stderr,"Usage: capture_check.exe capture.etl\n"); return 2; }
    EVENT_TRACE_LOGFILEW log{};
    log.LogFileName = argv[1];
    log.ProcessTraceMode = PROCESS_TRACE_MODE_EVENT_RECORD | PROCESS_TRACE_MODE_RAW_TIMESTAMP;
    log.EventRecordCallback = collect;
    auto handle = OpenTraceW(&log);
    if (handle == INVALID_PROCESSTRACE_HANDLE) { std::fprintf(stderr,"OpenTrace: %lu\n",GetLastError()); return 1; }
    auto result = ProcessTrace(&handle,1,nullptr,nullptr);
    CloseTrace(handle);
    if (result != ERROR_SUCCESS || callback_failed || log.LogfileHeader.EventsLost || log.LogfileHeader.BuffersLost || log.EventsLost) {
        std::fprintf(stderr,"Trace consumption failed: status=%lu eventsLost=%lu buffersLost=%lu callbackFailed=%d\n",result,log.LogfileHeader.EventsLost,log.LogfileHeader.BuffersLost,callback_failed); return 1;
    }
    try { verify(); }
    catch(const std::exception& e) { std::fprintf(stderr,"Capture failed: %s\n",e.what()); return 1; }
    return 0;
}
