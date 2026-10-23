# VN07 final bounded guard review — 400 words maximum
Read-onlyqualifiedopencode/nemotron-3-ultra-free, independentfamilyfromDeepSeekwriter. Previouslarge-sourceaudittimedoutwithNOreport; noimplementationoracceptancebythatattempt. ThisisoneboundedREAD-ONLYfollowupforfinalnewguard sourcebelow, notanothercoderepair. No tools, exploration, metadata, tests, edits oragents. ALLguardcodeinline; don'trequestomittedunchangedfiles orspeculatehowomittedhelperswork. Returnconcretescopefindingswithrepro orNO_CRITICAL_FINDINGS, <=400words. Codexaloneaccepts.
Caller/seamcontracts alreadyindependentlyverified: existing scan_spool/_inventory enforce report.segment_count<=max_records and segmentidentity/CRC/contiguousdurableH. Newproofcapture runsbeforeCverification/restore; proofssetBEFOREparentconstructor. Existingparentreaderconsumesexactopenedhandleguardreturns, closesit; readbatchboundedbyinitialfrozenH. RuntimecreatesALLreaders(inclC0)beforeinstall,reusesallbranches,revalidatesinventorybefore/afterrestore,andcatchBaseExceptionafterrestoreclosesengine. Actual183independenttestsPASSincludingC/newestsuffix/C0/lateropen/missingprefix/activegrowth/sealedchanges. Noaccount/GC/schema/liveorderschanges; doNOTclaimjointatomicFStransactionorhostattackersandbox.
Finitechecks onprovidedguardONLY: descriptoridentityboundratherthanpathstat; boundedmetadata/notpayloads; handlescloseonallerrors; activegrowthandsameinodesealallowed; onceANYsealedobservationexists, notsealedopenmustFAILeveniforiginalproofwasactiveandmtimeNone. Lastcriticalcounterexampleactive->sealed->activenowfixedby `elif number in self._sealed_observations`. Thatcasepassespublicreplayregressionclosed/replayed0. Checkactualcurrentcode, notoldcaptures. No broadunrelatedauditchecklist.
Source filevibetrading/local_runtime.py sha256=45d850cc31ef1a696cba5ccecb9fc36b902d25de95dae4d3c1fadd48135bf740

86: @dataclass(frozen=True)
87: class _SegmentProof:
88:     dev: int
89:     ino: int
90:     size: int
91:     mtime_ns: int | None
92:     active: bool

95: def _open_checkpoint_segment(root,number):
96:     sealed=Path(root)/f"{number:020d}.sealed"
97:     active=Path(root)/f"{number:020d}.active"
98:     # Reuse the bounded active/sealed rename-open sequence. Descriptor
99:     # metadata, not a path re-stat, authorizes the frozen segment.
100:     for path,is_sealed in ((sealed,True),(active,False),(sealed,True)):
101:         try:
102:             return path,is_sealed,path.open("rb")
103:         except FileNotFoundError:
104:             continue
105:     raise SpoolCorruption("checkpoint spool segment is missing")

108: def _capture_segment_proofs(root,report):
109:     """Pin only metadata for the frozen scan inventory; no payloads/index."""
110:     proofs={}
111:     for number in range(1,report.segment_count+1):
112:         _,sealed,opened=_open_checkpoint_segment(root,number)
113:         try:
114:             stat=os.fstat(opened.fileno())
115:             was_active=number==report.active_segment_id
116:             if not was_active and not sealed:
117:                 raise SpoolCorruption("checkpoint spool sealed segment regressed")
118:             proofs[number]=_SegmentProof(stat.st_dev,stat.st_ino,stat.st_size,
119:                 stat.st_mtime_ns if sealed else None,was_active)
120:         finally:
121:             opened.close()
122:     return proofs

125: class _CheckpointSpoolReader(SpoolReader):
126:     def __init__(self,root,producer_id,incarnation,*,proofs,**kwargs):
127:         # Proofs precede the superclass constructor, which may open segments
128:         # while locating C.
129:         self._proofs=dict(proofs)
130:         self._sealed_observations={number:(proof.size,proof.mtime_ns)
131:             for number,proof in self._proofs.items() if proof.mtime_ns is not None}
132:         super().__init__(root,producer_id,incarnation,**kwargs)
133:
134:     def _open_segment(self,number):
135:         proof=self._proofs.get(number)
136:         if proof is None:
137:             raise SpoolCorruption("checkpoint spool segment is not in frozen inventory")
138:         path,sealed,opened=super()._open_segment(number)
139:         try:
140:             stat=os.fstat(opened.fileno())
141:             if (stat.st_dev,stat.st_ino)!=(proof.dev,proof.ino):
142:                 raise SpoolCorruption("checkpoint spool segment identity changed")
143:             if proof.active:
144:                 # The active prefix may legitimately grow and later be sealed
145:                 # under the same inode. Never require active size/mtime equality.
146:                 if stat.st_size<proof.size:
147:                     raise SpoolCorruption("checkpoint spool active segment shrank")
148:                 if sealed:
149:                     signature=(stat.st_size,stat.st_mtime_ns)
150:                     observed=self._sealed_observations.get(number)
151:                     if observed is None:
152:                         self._sealed_observations[number]=signature
153:                     elif signature!=observed:
154:                         raise SpoolCorruption("checkpoint spool sealed segment changed")
155:                 elif number in self._sealed_observations:
156:                     raise SpoolCorruption("checkpoint spool sealed segment regressed")
157:             else:
158:                 if not sealed:
159:                     raise SpoolCorruption("checkpoint spool sealed segment regressed")
160:                 if (stat.st_size,stat.st_mtime_ns)!=(proof.size,proof.mtime_ns):
161:                     raise SpoolCorruption("checkpoint spool sealed segment changed")
162:             return path,sealed,opened
163:         except BaseException:
164:             opened.close()
165:             raise
166:
167:     def validate_inventory(self):
168:         for number in sorted(self._proofs):
169:             _,_,opened=self._open_segment(number)
170:             opened.close()
