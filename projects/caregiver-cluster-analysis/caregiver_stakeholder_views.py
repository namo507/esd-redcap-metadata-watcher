"""Plain-language figures and decision tables for the caregiver stakeholder notebook."""
from pathlib import Path
import html
import json
import textwrap
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import HTML, Markdown, display

NAMES = {'Profile 1': 'Support with more concern', 'Profile 2': 'More comfortable with screening'}
COLORS = {'Profile 1': '#B76423', 'Profile 2': '#235E83'}
DOMAIN_LABELS = {
    'Positive affect (more positive)': 'Feeling positive',
    'Emotional distress (less distress)': 'Less scared or sad',
    'Aversive feelings (less aversion)': 'Less angry or disgusted',
    'Visit feasibility (more feasible)': 'Visits feel manageable',
    'Result usefulness (more useful)': 'Results feel useful',
    'Ethical support (more support)': 'Screening feels right and fair',
}
YES = '#235E83'
PROBABLY = '#85B5D0'
OTHER = '#DEE4E8'
TEXT = '#263641'


def prepare(report, output):
    """Configure one restrained visual style and a run-specific aggregate folder."""
    report['output'] = Path(output)
    report['output'].mkdir(parents=True, exist_ok=True)
    (report['output'] / 'figures').mkdir(exist_ok=True)
    report['shown_tables'] = {}
    report['shown_figures'] = []
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
        'axes.titlesize': 15, 'axes.labelsize': 11, 'axes.titlepad': 17,
        'axes.spines.top': False, 'axes.spines.right': False,
        'axes.spines.left': False, 'axes.edgecolor': '#AAB4BC',
        'text.color': TEXT, 'axes.labelcolor': TEXT, 'xtick.color': TEXT,
        'ytick.color': TEXT, 'text.parse_math': False,
        'figure.facecolor': 'white', 'axes.facecolor': 'white',
        'figure.dpi': 115, 'savefig.facecolor': 'white'})


def table(report, name, data, formats=None):
    report['shown_tables'][name] = data.copy()
    styled = (data.style.hide(axis='index').format(formats or {}, na_rep='—')
        .set_table_attributes('style="width:100%;font-family:sans-serif;font-size:14px;border-collapse:collapse"')
        .set_properties(**{'text-align': 'left', 'padding': '10px 13px',
                           'white-space': 'normal', 'vertical-align': 'top'})
        .set_table_styles([
            {'selector': 'th', 'props': [('text-align', 'left'), ('padding', '11px 13px'),
                ('font-weight', '600'), ('background-color', '#EFF3F5'), ('color', TEXT),
                ('border-bottom', '2px solid #B8C4CB')]},
            {'selector': 'td', 'props': [('border-bottom', '1px solid #E2E7EA')]},
            {'selector': 'table', 'props': [('width', '100%'), ('font-family', 'sans-serif'),
                ('font-size', '14px'), ('border-collapse', 'collapse')]},
        ]))
    display(styled)


def finding(text, implication=None):
    message = f'**What this shows.** {text}'
    if implication:
        message += f'\n\n**Decision implication.** {implication}'
    display(Markdown(message))


def figure(report, fig, name, note=None):
    # Reserve a consistent footer for legends and notes so titles stay clear.
    handles, labels = [], []
    for ax in fig.axes:
        h, lab = ax.get_legend_handles_labels()
        for handle, label in zip(h, lab):
            if label not in labels:
                handles.append(handle); labels.append(label)
        if ax.get_legend() is not None:
            ax.get_legend().remove()
    for old in list(fig.legends): old.remove()
    if handles:
        columns = 1 if any(len(label) > 25 for label in labels) else min(3, len(labels))
        fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.55, .075),
                   ncol=columns, frameon=False, fontsize=10)
    if note:
        wrapped = '\n'.join(textwrap.wrap(note, max(85, int(fig.get_figwidth()*12))))
        fig.text(.01, .015, wrapped, ha='left', va='bottom', fontsize=9.5, color='#52616D')
    bottom = (.22 if columns == 1 else .19) if handles else .10 if note else 0
    fig.tight_layout(rect=(0, bottom, 1, 1))
    path = report['output'] / 'figures' / f'{name}.png'
    fig.savefig(path, dpi=160, bbox_inches='tight')
    report['shown_figures'].append(path)
    plt.show()
    plt.close(fig)


def _raw_number(report, field):
    return pd.to_numeric(report['raw'][field], errors='coerce').astype(float)


def _profile_value(report, key, measure, profile, column='Mean'):
    part = report[key]
    return float(part.loc[part.Measure.eq(measure) & part.Profile.eq(profile), column].iloc[0])


def _wilson(yes, n):
    if not n:
        return np.nan, np.nan
    p = yes/n; z = 1.959963984540054; d = 1+z*z/n
    mid = (p+z*z/(2*n))/d
    half = z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return 100*(mid-half), 100*(mid+half)


def show_overview(report):
    raw = report['raw']; intention = _raw_number(report, 'tfa_free_screen')
    broad = intention.ge(3).mean()*100
    concerned = _profile_value(report, 'heldout_table', 'Probably/definitely take screening (%)', 'Profile 1')
    display(Markdown(
        f'- **Start from broad interest:** {broad:.0f}% would probably or definitely take a free screening test.\n'
        f'- **Address concern within that support:** even the more-concerned pattern shows {concerned:.0f}% probable/definite interest.\n'
        '- **Discuss procedures and support separately:** general screening interest does not settle comfort with MRI, blood sampling, or the next steps.'))


def show_participants(report):
    raw = report['raw']; model = report['model_scores']
    data = pd.DataFrame({'Who is included': ['Final verified responses', 'Enough attitude information for profiles',
                                            'Retained in descriptions only'],
                         'Responses': [len(raw), len(model), len(raw)-len(model)]})
    table(report, 'participation', data)
    counts = raw.study.value_counts()
    fig, ax = plt.subplots(figsize=(9.5, 3.3))
    y = np.arange(len(counts))
    ax.barh(y, counts, height=.55, color=YES)
    ax.set_yticks(y, counts.index); ax.invert_yaxis()
    ax.set(xlabel='Verified survey responses', xlim=(0, counts.max()*1.25), title='Where the responses came from')
    ax.tick_params(axis='y', length=0)
    for i, n in enumerate(counts):
        ax.text(n+7, i, f'{n:,} ({n/len(raw):.1%})', va='center')
    figure(report, fig, '01_who_responded')
    finding(f'Global Online contributes {counts.get("Global Online",0)/len(raw):.1%} of verified responses. '
            'The sample combines recruitment channels with different connections to research.',
            'Broaden recruitment across settings before treating these proportions as the wider parent population.')


def show_interest(report):
    intention = _raw_number(report, 'tfa_free_screen').dropna()
    labels = ['Definitely yes', 'Probably', 'Unsure', 'Probably not', 'Definitely no']
    codes = [4,3,2,1,0]
    n = intention.value_counts().reindex(codes, fill_value=0)
    pct = n/len(intention)*100
    fig, ax = plt.subplots(figsize=(9.5, 3.7))
    ax.barh(range(5), pct, height=.58, color=[YES, PROBABLY, OTHER, OTHER, OTHER])
    ax.set_yticks(range(5), labels); ax.invert_yaxis()
    ax.set(xlim=(0,100), xlabel='Share of respondents (%)', title='Interest is broad; certainty is lower')
    ax.tick_params(axis='y',length=0)
    for i, (rate, count) in enumerate(zip(pct, n)):
        ax.text(rate+1.2, i, f'{rate:.1f}%  ·  {count} responses', va='center')
    figure(report, fig, '02_screening_interest', f'All {len(intention):,} observed answers. Hypothetical free screening for one’s four-month-old baby.')
    rows=[]
    for label,mask in [('Definitely yes',intention.eq(4)), ('Definitely or probably',intention.ge(3))]:
        count=int(mask.sum());lo,hi=_wilson(count,len(intention))
        rows.append({'Measure':label,'Responses':f'{count}/{len(intention)}','Share':f'{100*count/len(intention):.1f}%',
                     '95% range':f'{lo:.1f}–{hi:.1f}%'})
    table(report,'screening_interest',pd.DataFrame(rows))
    finding(f'{int(intention.eq(3).sum())} respondents say “probably.” That is openness with less certainty, '
            'and it should remain distinct from both definite interest and rejection.',
            'Ask what information, reassurance, or practical arrangements parents would need before deciding.')


def show_profiles(report):
    shares=report['share_table'].set_index('Profile')
    rows=[]
    for profile in NAMES:
        rate=_profile_value(report,'heldout_table','Probably/definitely take screening (%)',profile)
        lo=_profile_value(report,'heldout_table','Probably/definitely take screening (%)',profile,'Lower 95%')
        hi=_profile_value(report,'heldout_table','Probably/definitely take screening (%)',profile,'Upper 95%')
        share=shares.loc[profile]
        rows.append({'Attitude pattern':NAMES[profile],
            'Estimated share':f'{share["Share %"]:.1f}% ({share["Lower 95%"]:.1f}–{share["Upper 95%"]:.1f})',
            'Probably/definitely screen':f'{rate:.1f}% ({lo:.1f}–{hi:.1f})'})
    table(report,'profile_summary',pd.DataFrame(rows))
    data=report['profile_table'];labels=list(DOMAIN_LABELS)
    fig,ax=plt.subplots(figsize=(10.5,5.5)); y=np.arange(len(labels));h=.29
    for g,profile in enumerate(NAMES):
        part=data.loc[data.Profile.eq(profile)].set_index('Measure').reindex(labels)
        ypos=y+(g-.5)*.34
        ax.barh(ypos,part.Mean,height=h,color=COLORS[profile],label=NAMES[profile],alpha=.9,
                hatch='' if g==0 else '//',edgecolor='white')
        ax.hlines(ypos,part['Lower 95%'],part['Upper 95%'],color=TEXT,lw=1.3)
        for yy,mean,upper in zip(ypos,part.Mean,part['Upper 95%']):
            ax.text(upper+1,yy,f'{mean:.0f}',va='center',fontsize=10)
    ax.set_yticks(y,[DOMAIN_LABELS[k] for k in labels]);ax.invert_yaxis();ax.tick_params(axis='y',length=0)
    ax.set(xlim=(0,105),xticks=[0,25,50,75,100],xlabel='Average attitude rating (0–100; higher is more favorable)',
           title='Support is shared; feelings differ')
    ax.legend(loc='upper left',bbox_to_anchor=(0,1.18),frameon=False,ncol=1,fontsize=10)
    figure(report,fig,'03_attitude_patterns',f'{len(report["model_scores"]):,} classifiable responses. Thin lines are 95% uncertainty ranges; scores are not percentages of parents.')
    useful=_profile_value(report,'profile_table','Result usefulness (more useful)','Profile 1')
    ethical=_profile_value(report,'profile_table','Ethical support (more support)','Profile 1')
    finding(f'The more-concerned pattern still rates usefulness {useful:.0f}/100 and ethical support {ethical:.0f}/100. '
            'The two patterns describe different combinations of feelings and support; they overlap.',
            'Treat emotional concern as a topic to understand within support for screening. Avoid labeling this pattern “anti-screening.”')


def show_pattern_map(report):
    scores=report['model_scores']; posterior=report['posterior']
    x=scores['Visit feasibility (more feasible)']; y=100-scores['Emotional distress (less distress)']
    keep=x.notna() & y.notna()
    modal=posterior.idxmax(axis=1)
    fig,ax=plt.subplots(figsize=(9.4,5.3))
    for g,profile in enumerate(NAMES):
        ix=keep & modal.eq(profile)
        ax.scatter(x[ix],y[ix],s=24,alpha=.27,color=COLORS[profile],marker=['o','^'][g],
                   edgecolors='none',label=NAMES[profile])
    ax.set(xlim=(-2,102),ylim=(-2,102),xticks=[0,25,50,75,100],yticks=[0,25,50,75,100],
        xlabel='Visit manageability (0–100)',
        ylabel='Emotional concern (0–100)',
        title='Concern and visit manageability can coexist')
    ax.grid(alpha=.12);ax.legend(loc='upper left',bbox_to_anchor=(0,1.19),frameon=False,fontsize=10)
    figure(report,fig,'04_feasibility_and_concern',f'{int(keep.sum())} responses with both scores. Higher scores mean more manageable visits or more concern. Points can overlap; colors show the closest pattern.')
    finding('Similar visit-manageability ratings can accompany different levels of concern. '
            'This picture shows the answers that helped form the profiles; it is not independent validation.',
            'Ask about feelings and practical burden separately when designing parent conversations.')


def show_methods(report):
    data=report['method_table'];methods=['Saliva sampling','Video observation','Doctor observation',
                                       'Heart monitoring','Blood sampling','EEG','MRI']
    fig,ax=plt.subplots(figsize=(10.5,5.7));y=np.arange(len(methods));h=.27
    for g,profile in enumerate(NAMES):
        part=data[data.Profile.eq(profile)].set_index('Measure').reindex(methods)
        ypos=y+(g-.5)*.33
        ax.barh(ypos,part.Mean,height=h,color=COLORS[profile],label=NAMES[profile],alpha=.9,
                hatch='' if g==0 else '//',edgecolor='white')
        ax.hlines(ypos,part['Lower 95%'],part['Upper 95%'],color=TEXT,lw=1.2)
        for yy,mean,upper in zip(ypos,part.Mean,part['Upper 95%']):ax.text(upper+1,yy,f'{mean:.0f}',va='center',fontsize=10)
    ax.set_yticks(y,methods);ax.invert_yaxis();ax.tick_params(axis='y',length=0)
    ax.set(xlim=(0,100),xlabel='Perceived ease for the baby (0–100; higher means easier)',
           title='Procedure ratings do not follow a single ranking')
    ax.legend(loc='upper left',bbox_to_anchor=(0,1.16),frameon=False,fontsize=10)
    figure(report,fig,'05_method_ease','Method ratings did not create these profiles. Thin lines are 95% uncertainty ranges. These are ease ratings, not acceptance rates.')
    m=data.pivot(index='Measure',columns='Profile',values='Mean')
    finding(f'Saliva ratings differ ({m.loc["Saliva sampling","Profile 1"]:.0f} vs {m.loc["Saliva sampling","Profile 2"]:.0f}/100), '
            f'but blood ratings are similar ({m.loc["Blood sampling","Profile 1"]:.0f} vs {m.loc["Blood sampling","Profile 2"]:.0f}). '
            f'MRI is rated less easy in the more-comfortable pattern ({m.loc["MRI","Profile 2"]:.0f} vs {m.loc["MRI","Profile 1"]:.0f}).',
            'Discuss each procedure and its demands explicitly. Ask which tradeoffs parents would accept where scientifically feasible.')


def _context_rates(report, attribute, categories):
    intention=_raw_number(report,'tfa_free_screen')
    rows=[]
    for category,label in categories:
        x=intention.loc[report['covariates'][attribute].eq(category)].dropna()
        if len(x)<20: continue
        definite=int(x.eq(4).sum());probable=int(x.eq(3).sum());lo,hi=_wilson(definite+probable,len(x))
        rows.append({'Group':label,'n':len(x),'Definitely %':100*definite/len(x),'Probably %':100*probable/len(x),
                     'Other %':100*x.le(2).sum()/len(x),'Interested %':100*x.ge(3).mean(),
                     'Interested lower':lo,'Interested upper':hi})
    return pd.DataFrame(rows)


def show_context(report):
    income=_context_rates(report,'income_band',[("<=$60k",'≤$60,000'),('$60–100k','$60,001–100,000'),('>$100k','>$100,000')])
    race=_context_rates(report,'race_ethnicity',[("White non-Hispanic",'White, non-Hispanic'),
        ('Black non-Hispanic','Black, non-Hispanic'),('Hispanic','Hispanic/Latino'),
        ('Multiracial or another race','Multiracial / another race')])
    fig,axes=plt.subplots(1,2,figsize=(12.2,4.8))
    for ax,data,title in zip(axes,[income,race],['Household income','Race / ethnicity']):
        y=np.arange(len(data));left=np.zeros(len(data))
        for column,color,label in [('Definitely %',YES,'Definitely yes'),('Probably %',PROBABLY,'Probably'),('Other %',OTHER,'Unsure / no')]:
            ax.barh(y,data[column],left=left,color=color,height=.55,label=label,edgecolor='white')
            for yy,value,base in zip(y,data[column],left):
                if value>=9:ax.text(base+value/2,yy,f'{value:.0f}%',ha='center',va='center',fontsize=9.5,color='white' if color==YES else TEXT)
            left+=data[column].to_numpy()
        ax.errorbar(data['Interested %'],y+.32,xerr=np.vstack([data['Interested %']-data['Interested lower'],
            data['Interested upper']-data['Interested %']]),fmt='o',ms=3,mfc='white',mec=TEXT,color=TEXT,lw=1,capsize=3)
        ax.set_yticks(y,[f'{g}\n(n={n})' for g,n in zip(data.Group,data.n)])
        ax.invert_yaxis();ax.tick_params(axis='y',length=0)
        ax.set(xlim=(0,100),xlabel='Share of observed answers (%)',title=title)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles[:3],labels[:3],loc='upper center',ncol=3,frameon=False,bbox_to_anchor=(.5,1.02),fontsize=10)
    figure(report,fig,'06_context_and_interest','Black marks show 95% ranges for total definite/probable interest. Descriptive comparisons; unknown answers are omitted from these panels.')
    table_data=pd.concat([income.assign(Context='Income'),race.assign(Context='Race / ethnicity')],ignore_index=True)
    report['shown_tables']['context_interest_detail']=table_data
    finding('Definite interest is lower in the middle income bracket than in either adjacent bracket. '
            'Black respondents report less definite interest than White respondents, but more combined definite/probable interest.',
            'Keep certainty and openness separate. Ask what remains unresolved rather than assigning attitudes from income or race.')
    display(Markdown('#### What do doctor confidence and values add?'))
    adjusted=report['adjusted_associations']
    values=adjusted[adjusted.Family.eq('Values')].copy()
    values=values.loc[values['Difference pp'].abs().sort_values(ascending=False).index].head(3)
    trust=adjusted[adjusted.Contrast.str.startswith('Screening-specific trusting item')]
    family=adjusted[adjusted.Contrast.eq('Autistic child: yes vs no')]
    selected=pd.concat([trust,family,values])
    label_map={'conformity':'Respect and obedience','universalism':'Equal opportunities',
               'power':'Importance of being in charge','benevolence':'Helping others',
               'stimulation':'Adventure and risk','tradition':'Religious tradition',
               'self direction':'Curiosity and understanding','security':'Order and cleanliness',
               'achievement':'Achievement','hedonism':'Pleasure and fun'}
    rows=[]
    for _,r in selected.iterrows():
        label=('Higher confidence in the doctor' if r.Family=='Context' else 'Having an autistic child' if r.Family=='Demographics'
               else label_map.get(r.Contrast.split('Value: ')[1].split(' (')[0],r.Contrast))
        rows.append({'Separately measured response':label,
            'Estimated difference in more-comfortable pattern':f'{r["Difference pp"]:+.1f} percentage points',
            'Analysis n':int(r['n']),
            '95% range':f'{r["Refit 95% low"]:+.1f} to {r["Refit 95% high"]:+.1f}'})
    table(report,'selected_context_signals',pd.DataFrame(rows))
    display(Markdown('Positive numbers indicate a stronger link to the more-comfortable pattern; negative numbers indicate the more-concerned pattern. '
        '**Decision implication:** ask about previous experience, confidence in the doctor, and personal priorities. '
        'These associations do not reveal motives or show that any message will change a decision.'))
    display(HTML('<details><summary>How to read these selected context comparisons</summary>'
        '<p>Estimates describe percentage-point differences in modeled pattern probability, after accounting for measured '
        'income, education, race/ethnicity, recruitment source, and having an autistic child. '
        'Doctor confidence compares 100 versus 75 on its 0–100 scale. The family comparison is having an autistic child versus not having one. '
        'Values compare the upper versus lower quartile of ratings relative to each person’s other value answers. '
        'The three value rows have the largest absolute estimated contrasts; all ten comparisons remain in the analyst reference. '
        'The intervals include sampling and profile-refitting variation; these exploratory selected results do not establish causes.</p></details>'))


def show_early_support(report):
    intention=_raw_number(report,'tfa_free_screen');importance=_raw_number(report,'tfa_intervene')
    groups=[('Probably / definitely screening',intention.ge(3)),('Unsure / probably not / definitely no',intention.le(2))]
    rows=[]
    for label,mask in groups:
        answers=importance.loc[mask & importance.between(1,4)]; n=len(answers)
        very=int(answers.eq(4).sum());some=int(answers.eq(3).sum());lo,hi=_wilson(very+some,n)
        rows.append({'Screening response':label,'n':n,'Very important %':100*very/n,
            'Somewhat important %':100*some/n,'Not very / not important %':100*answers.le(2).sum()/n,
            'Important n':very+some,'Important %':100*(very+some)/n,'Lower':lo,'Upper':hi})
    data=pd.DataFrame(rows)
    fig,ax=plt.subplots(figsize=(10.5,3.6));left=np.zeros(len(data));y=np.arange(len(data))
    for col,color,label in [('Very important %',YES,'Very important'),('Somewhat important %',PROBABLY,'Somewhat important'),
                            ('Not very / not important %',OTHER,'Not very / not important')]:
        ax.barh(y,data[col],left=left,height=.48,color=color,label=label,edgecolor='white')
        for yy,value,base in zip(y,data[col],left):
            if value>=9:ax.text(base+value/2,yy,f'{value:.0f}%',ha='center',va='center',color='white' if color==YES else TEXT,fontsize=10)
        left+=data[col].to_numpy()
    ax.set_yticks(y,[f'{textwrap.fill(g,31)}\n(n={n})' for g,n in zip(data['Screening response'],data.n)])
    ax.invert_yaxis();ax.tick_params(axis='y',length=0)
    ax.set(xlim=(0,100),xlabel='How important is early help and support? (%)',
           title='Screening hesitation can coexist with support for early help')
    ax.legend(loc='upper left',bbox_to_anchor=(0,1.28),ncol=2,frameon=False,fontsize=10)
    figure(report,fig,'07_screening_and_early_support','All verified responses with both answers. Importance of early support is not willingness to join an intervention trial.')
    report['shown_tables']['screening_and_early_support_detail']=data
    other=data.iloc[1]
    finding(f'{int(other["Important n"])}/{int(other.n)} respondents who are unsure or less interested in screening '
            f'still rate early support somewhat/very important ({other["Important %"]:.1f}%; 95% range {other.Lower:.1f}–{other.Upper:.1f}%).',
            'Discuss identification and early support as separate choices. Aim 2 trial willingness still needs direct study.')


def show_decisions(report):
    interested=_profile_value(report,'heldout_table','Probably/definitely take screening (%)','Profile 1')
    rows=[
        {'Priority':'1. Understand concern within support','Evidence':f'{interested:.0f}% screening interest in the more-concerned pattern.',
         'Proposal for the next study':'Co-design explanations of results, uncertainty, and available support with parents and autistic stakeholders.',
         'What to test':'Which concerns parents say the materials address, and whether the decision feels informed.'},
        {'Priority':'2. Review procedure-specific burden','Evidence':'Blood ratings are similar across patterns; MRI does not follow the general-support ranking.',
         'Proposal for the next study':'Ask about duration, sensory demands, visit scheduling, and feasible procedure choices.',
         'What to test':'Acceptability of each actual procedure and its tradeoffs.'},
        {'Priority':'3. Broaden whose perspectives are heard','Evidence':f'{report["raw"].study.eq("Global Online").mean():.0%} of verified responses came from Global Online.',
         'Proposal for the next study':'Recruit and interview across community settings; monitor family experience, income, race/ethnicity, and recruitment source.',
         'What to test':'Whether additional perspectives change the attitude patterns and practical priorities.'},
        {'Priority':'4. Separate screening from early support','Evidence':'Most respondents unsure or less interested in screening still value early help.',
         'Proposal for the next study':'Discuss these choices separately and directly ask about prediagnostic trial participation.',
         'What to test':'Screening acceptability, support preferences, and trial willingness as distinct outcomes.'},
    ]
    table(report,'leadership_decisions',pd.DataFrame(rows))
    display(Markdown('**Recommended next step:** use these four proposals to structure parent and autistic-stakeholder review before changing recruitment materials or study procedures.'))


def export_aggregate_reference(report):
    out=report['output']
    for name,data in report['shown_tables'].items():data.to_csv(out/f'{name}.csv',index=False)
    for name in ['profile_table','method_table','heldout_table','share_table','adjusted_associations']:
        report[name].to_csv(out/f'analyst_{name}.csv',index=False)
    (out/'provenance.json').write_text(json.dumps(report['provenance'],indent=2))
