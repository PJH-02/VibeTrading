"""Static figures for the read-only FV diagnosis."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def plot(output):
    r=json.loads((output/'prediction_diagnosis.json').read_text(encoding='utf-8'))
    keys=sorted({x['venue_strategy'] for x in r['horizons']})
    horizons=sorted({x['horizon_seconds'] for x in r['horizons']})
    values=np.array([[next(x['rmse_reduction_percent'] for x in r['horizons']
        if x['venue_strategy']==k and x['horizon_seconds']==h) for h in horizons] for k in keys])
    fig,ax=plt.subplots(figsize=(13,8),layout='constrained')
    im=ax.imshow(values,cmap='RdYlGn',vmin=-5,vmax=5,aspect='auto')
    for i in range(len(keys)):
        for j in range(len(horizons)):
            ax.text(j,i,f'{values[i,j]:.2f}',ha='center',va='center',fontsize=9,
                    color='white' if abs(values[i,j])>=4 else '#1b2530')
    ax.set_xticks(range(len(horizons)),['100ms','500ms','1s','2s','5s','10s','30s','60s','120s*','300s*'])
    ax.set_yticks(range(len(keys)),[k.replace(':',' / ').upper() for k in keys])
    ax.set_xlabel('Forecast horizon; * longer horizons have fewer than 10 inference blocks')
    fig.colorbar(im,ax=ax,label='RMSE reduction vs unchanged mid (%) / colors saturate at +/-5')
    ax.set_title('Frozen 30s FV coefficients across receipt-time horizons\nPositive = smaller error; values printed without color clipping',loc='left',pad=15)
    fig.savefig(output/'horizon_rmse.png',dpi=160);plt.close(fig)

    d=json.loads((output/'s2_lambda_decomposition.json').read_text())
    venues=list(d);x=np.arange(len(venues));width=.24
    fig,ax=plt.subplots(figsize=(10,5.5),layout='constrained')
    ax.axhspan(0,1,color='#dcebdc',alpha=.6,label='Allowed convergence range (0, 1]')
    fields=[('lambda_total','Current fitted coefficient','#bc6147'),
            ('lambda_gap_closure_component','Gap-closure component','#247b96'),
            ('lambda_anchor_future_movement_component','Anchor-movement component','#8c85a4')]
    for i,(field,label,color) in enumerate(fields):
        vals=[d[v][field] for v in venues]
        bars=ax.bar(x+(i-1)*width,vals,width,label=label,color=color)
        ax.bar_label(bars,fmt='%.3f',padding=4,fontsize=9)
    ax.axhline(0,color='#777',lw=.7);ax.set_xticks(x,[v.upper() for v in venues])
    ax.set_ylabel('Contribution to zero-intercept fitted coefficient')
    ax.set_ylim(-1.05,2.2)
    ax.legend(loc='upper left',fontsize=9,ncol=2)
    ax.spines[['top','right']].set_visible(False)
    ax.set_title('S2: absolute-price regression mixes gap closure with Binance movement\nEx-post training-label decomposition; future Binance is never an input',loc='left')
    fig.savefig(output/'s2_lambda_decomposition.png',dpi=160);plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path)
    plot(p.parse_args().output)
