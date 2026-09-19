"""Reproducible synthetic, non-preemptive queue comparison; no clinical claims."""
import heapq
import random
import statistics
import math


def generate(seed=42,count=120):
    rng=random.Random(seed)
    arrival=0
    patients=[]
    for i in range(count):
        arrival+=rng.expovariate(1/4)
        patients.append({'id':i,'arrival':arrival,'duration':rng.uniform(5,35),
                         'urgency':rng.choices([1,2,3,4,5],[1,2,3,5,8])[0]})
    return patients


def simulate(patients,policy,doctors=2):
    if policy not in ('FCFS','Urgency with aging') or doctors<1:
        raise ValueError('Choose a supported policy and at least one doctor.')
    arrivals=sorted(patients,key=lambda p:(p['arrival'],p['id']))
    waiting=[]
    servers=[(0,i) for i in range(doctors)]
    heapq.heapify(servers)
    index=0
    result=[]
    clock=0
    while index<len(arrivals) or waiting:
        now,doctor=heapq.heappop(servers)
        now=max(now,clock)
        if not waiting and index<len(arrivals):
            now=max(now,arrivals[index]['arrival'])
        while index<len(arrivals) and arrivals[index]['arrival']<=now:
            waiting.append(arrivals[index]);index+=1
        clock=now
        def priority(p):
            if policy=='FCFS': return (p['arrival'],p['id'])
            # Routine patients age toward level 3, never above emergency levels.
            effective=p['urgency'] if p['urgency']<=2 else max(3,p['urgency']-int((now-p['arrival'])//30))
            return (effective,p['arrival'],p['id'])
        selected=min(waiting,key=priority)
        waiting.remove(selected)
        result.append({**selected,'start':now,'wait':now-selected['arrival'],'doctor':doctor})
        heapq.heappush(servers,(now+selected['duration'],doctor))
    return result


def compare(seed=42,count=120,doctors=2):
    patients=generate(seed,count)
    results=[]
    for policy in ('FCFS','Urgency with aging'):
        rows=simulate(patients,policy,doctors)
        for urgency in range(1,6):
            waits=sorted(r['wait'] for r in rows if r['urgency']==urgency)
            if waits:
                results.append({'Policy':policy,'Urgency':urgency,'Patients':len(waits),
                                'Mean wait (min)':round(statistics.mean(waits),1),
                                'P95 wait (min)':round(waits[max(0,math.ceil(.95*len(waits))-1)],1)})
    return results
