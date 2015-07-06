from pydruid import client
from pydruid.utils.filters import Dimension, Filter
from dateutil.parser import parse
from datetime import datetime, timedelta
from flask import Flask, render_template, request, flash
from flask_bootstrap import Bootstrap
from wtforms import Form, SelectMultipleField, SelectField, TextField
import pandas as pd
from pandas_highcharts.core import serialize

pd.set_option('display.max_colwidth', -1)
since_l = {
    '1hour': timedelta(hours=1),
    '1day': timedelta(days=1),
    '7days': timedelta(days=7),
    '28days': timedelta(days=28),
    'all': timedelta(days=365*100)
}

metric = "count"


class DruidDataSource(object):

    def __init__(self, name):
        self.name = name
        self.cols = self.latest_metadata()
        self.col_names = sorted([col for col in self.cols.keys()])

    def latest_metadata(self):
        max_time = query.time_boundary(
            datasource=self.name)[0]['result']['maxTime']
        max_time = parse(max_time)
        intervals = (max_time - timedelta(seconds=1)).isoformat() + '/'
        intervals += max_time.isoformat()
        return query.segment_metadata(
            datasource=self.name,
            intervals=intervals)[-1]['columns']

def form_factory(datasource, form_args=None):
    grain = ['all', 'none', 'minute', 'hour', 'day']
    limits = [0, 5, 10, 25, 50, 100, 500]

    if form_args:
        limit = form_args.get("limit")
        try:
            limit = int(limit)
            if limit not in limits:
                limits.append(limit)
                limits = sorted(limits)
        except:
            pass

    class QueryForm(Form):
        viz_type = SelectField(
            'Viz', choices=[(k, v.verbose_name) for k, v in viz_types.items()])
        groupby = SelectMultipleField(
            'Group by', choices=[(m, m) for m in datasource.col_names])
        granularity = SelectField(
            'Granularity', choices=[(g, g) for g in grain])
        since = SelectField(
            'Since', choices=[(s, s) for s in since_l.keys()])
        limit = SelectField(
            'Limit', choices=[(s, s) for s in limits])
        flt_col_1 = SelectField(
            'Filter 1', choices=[(m, m) for m in datasource.col_names])
        flt_op_1 = SelectField(
            'Filter 1', choices=[(m, m) for m in ['==', '!=', 'in',]])
        flt_eq_1 = TextField("Super")
    return QueryForm


class BaseViz(object):
    verbose_name = "Base Viz"
    template = "panoramix/datasource.html"
    def __init__(self, datasource, form_class, form_data):
        self.datasource = datasource
        self.form_class = form_class
        self.form_data = form_data
        self.df = self.bake_query()
        if self.df is not None:
            self.df.timestamp = pd.to_datetime(self.df.timestamp)
            self.df_prep()
            self.form_prep()

    def bake_query(self):
        ds = self.datasource
        args = self.form_data
        groupby = args.getlist("groupby") or []
        granularity = args.get("granularity")
        metric = "count"
        limit = int(args.get("limit", ROW_LIMIT)) or ROW_LIMIT
        since = args.get("since", "all")
        from_dttm = (datetime.now() - since_l[since]).isoformat()

        # Building filters
        i = 1
        filters = None
        while True:
            col = args.get("flt_col_" + str(i))
            op = args.get("flt_op_" + str(i))
            eq = args.get("flt_eq_" + str(i))
            if col and op and eq:
                cond = None
                if op == '==':
                    cond = Dimension(col)==eq
                elif op == '!=':
                    cond = ~(Dimension(col)==eq)
                elif op == 'in':
                    fields = []
                    for s in eq.split(','):
                        s = s.strip()
                        fields.append(Filter.build_filter(Dimension(col)==s))
                    cond = Filter(type="or", fields=fields)


                if filters:
                    filters = cond and filters
                else:
                    filters = cond
            else:
                break
            i += 1

        kw = {}
        if filters:
            kw['filter'] = filters
        query.groupby(
            datasource=ds.name,
            granularity=granularity or 'all',
            intervals=from_dttm + '/' + datetime.now().isoformat(),
            dimensions=groupby,
            aggregations={"count": client.doublesum(metric)},
            #filter=filters,
            limit_spec={
                "type": "default",
                "limit": limit,
                "columns": [{
                    "dimension" : metric,
                    "direction" : "descending",
                },],
            },
            **kw
        )
        return query.export_pandas()


    def df_prep(self, ):
        pass

    def form_prep(self):
        pass

    def render(self, *args, **kwargs):
        form = self.form_class(self.form_data)
        return render_template(
            self.template, form=form)


class TableViz(BaseViz):
    verbose_name = "Table View"
    template = 'panoramix/viz_table.html'
    def render(self):
        form = self.form_class(self.form_data)
        if self.df is None or self.df.empty:
            flash("No data.", "error")
            table = None
        else:
            if self.form_data.get("granularity") == "all":
                del self.df['timestamp']
            table = self.df.to_html(
                classes=["table", "table-striped", 'table-bordered'],
                index=False)
        return render_template(
            self.template, form=form, table=table)


class HighchartsViz(BaseViz):
    verbose_name = "Base Highcharts Viz"
    template = 'panoramix/viz_highcharts.html'
    chart_kind = 'line'
    def render(self, *args, **kwargs):
        form = self.form_class(self.form_data)
        if self.df is None or self.df.empty:
            flash("No data.", "error")
        else:
            table = self.df.to_html(
                classes=["table", "table-striped", 'table-bordered'],
                index=False)
        return render_template(
            self.template, form=form, table=table,
            *args, **kwargs)


class TimeSeriesViz(HighchartsViz):
    verbose_name = "Time Series - Line Chart"
    chart_kind = "line"
    def render(self):
        df = self.df
        df = df.pivot_table(
            index="timestamp",
            columns=[
                col for col in df.columns if col not in ["timestamp", metric]],
            values=[metric])
        chart_js = serialize(
            df, kind=self.chart_kind, **CHART_ARGS)
        return super(TimeSeriesViz, self).render(chart_js=chart_js)


class TimeSeriesAreaViz(TimeSeriesViz):
    verbose_name = "Time Series - Area Chart"
    chart_kind = "area"


class DistributionBarViz(HighchartsViz):
    verbose_name = "Distribution - Bar Chart"
    chart_kind = "bar"
    def render(self):
        df = self.df
        df = df.pivot_table(
            index=[
                col for col in df.columns if col not in ['timestamp', metric]],
            values=[metric])
        df = df.sort(metric, ascending=False)
        chart_js = serialize(
            df, kind=self.chart_kind, **CHART_ARGS)
        return super(DistributionBarViz, self).render(chart_js=chart_js)

viz_types = {
    'table': TableViz,
    'line': TimeSeriesViz,
    'area': TimeSeriesAreaViz,
    'dist_bar': DistributionBarViz,
}


@app.route("/datasource/<name>/")
def datasource(name):
    viz_type = request.args.get("viz_type", "table")
    datasource = DruidDataSource(name)
    viz = viz_types[viz_type](
        datasource,
        form_class=form_factory(datasource, request.args),
        form_data=request.args)
    return viz.render()


if __name__ == '__main__':
    app = Flask(__name__)
    app.secret_key = "monkeys"
    Bootstrap(app)

    app.debug = True
    app.run(host='0.0.0.0', port=PORT)
