import pathlib,subprocess
r=pathlib.Path('/srv/vibetrading-capacity/incident-20261006')
for name in ('vibetrading-maintenance-1','vibetrading-crypto-maintenance-1'):
 pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',name],text=True).strip();s=subprocess.check_output([str(r/'py-spy'),'dump','--pid',pid],text=True);(r/(name+'-active-stack.txt')).write_text(s);print(name,s[-5500:])
