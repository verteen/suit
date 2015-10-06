"""

                                                    Suit Template Engine
@author:    Andrey Yurjev (ayurjev)
@date:      26.11.2013
@version:   1.1

#################################################       Suit scheme:          ##########################################

                            Programmer                                       Client
*                                V                           *                  |
*                        Source Templates                    *                  |
*                                |                           *                  |
*                                V                           *                  |
*                            Compiler                        *                  |
*                                |                           *                  |
*                                V                           *                  |
*                            Template                        *                  |
*                                |                           *                  |
*                                V                           *                  |
* XmlTag  <-----------------> TemplatePart                   *                  |
*    SuitTag                     |                           *                  V
*        Variable                |                           *              Suit/suit
*        Condition               |                           *                  |
*        ...                     |                           *                  |
*            |                   V                           *                  V                       SuitNone
*            V--------------> SyntaxEngine           -----------------> Compiled Templates <----------- SuitFilters
*                                PythonSyntaxEngine          *                  |                       SuitRuntime
*                                JavascriptSyntaxEngine      *                  |
*                                ...                         *                  V
*                                                            *                 HTML
*                                                            *
***********************************************************************************************************************
"""

import re
import os
import json
import importlib
from html import escape, unescape
from abc import ABCMeta, abstractmethod
from datetime import datetime, date, time


SuitTags = [
    "var", "if", "list", "breakpoint", "expression", "condition", "true", "false", "iterationvar", "iterationkey"
]


class TemplateParseError(Exception):
    pass


class TemplateNotFound(Exception):
    pass


class TagCounter(object):
    """ Counts/decounts a nested tags """

    def __init__(self, tags_to_process=None):
        self.tags_to_process = tags_to_process or SuitTags
        self.maxI = 0

    def count(self, expression):
        """
        Enumerates all the tags found in given expression, so <p><p></p></p> becomes <p_1><p_2></p_2></p_1>
        :param expression:  Expression with some tags inside
        :return:            Enumerated result
        """
        try:
            stack = []
            self.maxI = 0
            p = re.compile("<(/*(%s))(\s|>)+" % "|".join(self.tags_to_process), re.DOTALL)
            return re.sub(p, lambda tagMatch: self._manageStack(tagMatch, stack), expression)
        except IndexError:
            raise TemplateParseError("opening/closing tags missmatch found: %s" % expression)

    def decount(self, template):
        """
        Cleans up all enumerations from tags
        :param template:  Enumerated template
        :return:          Cleaned template
        """
        template = re.sub("<\w+(_\d+)[\s|>]+", lambda m: m.group(0).replace(m.group(1), ""), template)
        template = re.sub("</\w+(_\d+)>", lambda m: m.group(0).replace(m.group(1), ""), template)
        return template

    def _manageStack(self, tagMatch, stack):
        """
        Controls the stack of opening/closing brackets during the count() operation
        :param tagMatch: Founded tag (opening or closing)
        :param stack:    Current stack
        :return:
        """
        tagMatch, tag = tagMatch.group(0), tagMatch.group(1)
        if tag.startswith("/"):
            return tagMatch.replace(tag, "%s_%s" % (tag, stack.pop()))
        else:
            newI = self.maxI
            stack.append(newI)
            self.maxI += 1
            return tagMatch.replace(tag, "%s_%s" % (tag, newI))


class XmlTag(object):
    """
    Base class of the tags hierarchy.
    It represents a ordinary xml tag without any template engine logic.
    """

    def __init__(self, stringTag):
        self.stringTag = re.sub("\s\s+", " ", stringTag).strip()
        self.firstLine = self.parseFirstLine(self.stringTag)
        self.name = self.parseTagName(self.firstLine)
        self.attributes = self.parseAttributes(self.firstLine)
        self.body = self.parseBody(self.name, self.firstLine, self.stringTag)
        self.name = self.name.split("_")[0]

    def get(self, attrName):
        """
        Returns an attribute value by given attribute name
        :param attrName: name of the attribute
        :return:         attribute value
        """
        return self.attributes.get(attrName)

    def parseFirstLine(self, expression):
        """
        Returns the first line of the tag (opening part between < and > with all attributes)
        :return: str:
        """
        quotes = None
        firstLine, stack, quotes_opened1, quotes_opened2 = "", 0, False, False
        for char in expression:
            if char == "<" and (quotes_opened1 is False and quotes_opened2 is False):
                stack += 1
            elif char == ">" and (quotes_opened1 is False and quotes_opened2 is False):
                stack -= 1
                if stack == 0:
                    firstLine += char
                    break
            elif char == "'":
                if quotes is None:
                    quotes = "'"
                if quotes == "'":
                    quotes_opened1 = quotes_opened1 is False
            elif char == '"':
                if quotes is None:
                    quotes = '"'
                if quotes == '"':
                    quotes_opened2 = quotes_opened2 is False
            firstLine += char
        return firstLine

    def parseTagName(self, firstLine):
        """ Returns name of the tag """
        return self._map_replace(firstLine.split(" ")[0], {"<": "", ">": ""})

    def parseAttributes(self, firstLine):
        """ Returns attributes of the tag in a map """
        result = {}
        matches = re.findall('''\s(.+?)=(?P<quote>\"|')(.*?)(?P=quote)+''', firstLine, re.DOTALL)
        if len(matches) > 0:
            for match in matches:
                result[match[0]] = match[2]
        return result

    def parseBody(self, tagName, firstLine, expression):
        """ Returns body of the tag represented by TemplatePart instance """
        return self._map_replace(expression, {firstLine: "", "</%s>" % tagName: ""}).strip()

    def _map_replace(self, string, repl_map):
        for hs in repl_map:
            string = string.replace(hs, repl_map[hs])
        return string


class Variable(XmlTag):
    """ Represents an ordinary variables """

    def __init__(self, tag_string):
        super().__init__(tag_string)
        self.var_name = self._convertVarPath(self.body)
        self.default = self.attributes.get("d")
        self.filters = self.get_filters()

    def get_filters(self):
        """
        Returns a list of the (filterName, filterParams) that should be applied to the variable
        :return: list:
        """
        filters = self.attributes.get("filter") or ""
        result = [(f.strip(), self.attributes.get("%s-data" % f.strip())) for f in filters.split(",")]
        return list(filter(lambda it: it[0] not in [None, "None", ""], [(f[0], f[1]) for f in result]))

    def _convertVarPath(self, varDottedNotation):
        """
        Converts the call to a variable from dot-notation to brackets-notation
        :param varDottedNotation:   Variable    user.name
        :return:                    Result      ["user"]["name"]
        """
        varDottedNotation = varDottedNotation.strip(".")
        varDottedNotation = varDottedNotation.replace(".[", "[")
        varDottedNotation = re.sub("\.+", ".", varDottedNotation)

        tmp = re.sub('''\[.+?\]''', lambda m: "." + m.group(0), varDottedNotation)
        tmp = tmp.replace("].[", '''][''')
        tmp = tmp.replace(".[", '''"][''')
        tmp = tmp.replace("].", ''']["''')
        tmp = tmp.replace(".", '''"]["''')
        result = '''["''' + tmp + ('''"]''' if tmp.endswith("]") is False else "")
        return result


class IterationVariable(Variable):
    """ Represents an iteration variable """

    def _convertVarPath(self, varDottedNotation):
        # we dont interested in parameter varDottedNotation
        # since we have all required data in attributes of current tag
        varDottedNotation = ".".join(
            list(
                filter(
                    lambda m: m != "None",
                    [self.attributes.get("in"), self.attributes.get("name"), self.attributes.get("path")]
                )
            )
        )
        res = super()._convertVarPath(varDottedNotation)
        res = re.sub('\["%s"\]' % self.attributes.get("name"), '[%s]' % self.attributes.get("name"), res)
        return res


class IterationKey(Variable):
    """ Represents an iteration key """

    def __init__(self, tag_string):
        super().__init__(tag_string)
        self.var_name = self.attributes.get("name") + (
            self.attributes.get("mod") if self.attributes.get("mod") is not None else ""
        )


class Condition(XmlTag):
    """ Represents a condition expression """

    def __init__(self, tagString):
        super().__init__(tagString)
        self.condition = TemplatePart(
            self.attributes.get("condition")
        ) if self.attributes.get("condition") is not None else None

        self.true = TemplatePart(self.body)
        self.false = TemplatePart("")
        for t in TemplatePart(self.body).getTags():
            if t.name == "condition":
                self.condition = TemplatePart(t.body)
            elif t.name == "true":
                self.true = TemplatePart(t.body)
            elif t.name == "false":
                self.false = TemplatePart(t.body)


class Expression(XmlTag):
    """ Represents a simple expression that can be avaluated """

    def __init__(self, tag_string):
        super().__init__(tag_string)
        self.expresion_body = TemplatePart(self.body)


class List(XmlTag):
    """ Represents an iteration cycle """

    def __init__(self, tagString):
        super().__init__(tagString)
        # parsing itervar
        itervar = self.attributes.get("for")
        if itervar.find(",") > -1:
            self.dict_iteration = True
            self.iterkey, self.iterval = itervar.replace(" ", "").split(",")
        else:
            self.dict_iteration = False
            self.iterkey, self.iterval = itervar, itervar

        # parsing iterable
        iterable = self.attributes.get("in")
        if iterable.startswith("<var"):
            self.iterable = Variable(iterable)
            self.iterable_name = Variable(iterable).body
        else:
            self.iterable = Variable("<var>%s</var>" % iterable)
            self.iterable_name = iterable

        # parsing template
        self.iteration_template = TemplatePart(self.rename_iteration_variables(self.body))

    def rename_iteration_variables(self, template):
        # converting nested lists iterables
        template = re.sub(
            '''\sin=["|']%s(.*?)["|']''' % self.iterval,
            lambda m: " in='%s[%s]%s'" % (
                self.iterable_name,
                self.iterkey if self.iterkey is not None else self.iterval,
                m.group(1)
            ),
            template
        )

        template = re.sub(
            '''>%s(\..+?)?<''' % self.iterval,
            lambda m: ">%s[%s]%s<" % (
                self.iterable_name,
                self.iterkey if self.iterkey is not None else self.iterval,
                m.group(1) if m.group(1) else ""
            ),
            template
        )

        # iteration counter
        template = re.sub(
            "<var(?P<counter>(_\d+)?)>i</var(?P=counter)>",
            lambda m: "<iterationkey type='key' mod=' + 1' name='%s'></iterationkey>" % self.iterval,
            template
        )

        # converting itervals
        template = re.sub(
            "<var(?P<counter>(?:_[\d]+)?)([^>]*)?>%s([.|\[][^<]+)?</var(?P=counter)>" % self.iterval,
            lambda m: "<iterationvar type='value' in='%s' name='%s' path='%s'%s></iterationvar>" % (
                self.iterable_name,
                self.iterkey if self.iterkey is not None else self.iterval,
                m.group(3),
                m.group(2) if m.group(2) is not None else ""
            ),
            template
        )

        # converting iterkeys
        if self.dict_iteration:
            template = re.sub(
                "<var(?P<counter>(?:[_\d])*)>%s</var(?P=counter)>" % self.iterkey,
                lambda m: "<iterationkey type='key' name='%s'></iterationkey>" % self.iterkey,
                template
            )
        return template


class Breakpoint(XmlTag):
    def __init__(self, tag_string):
        super().__init__(tag_string)
        self.isInclude = self.attributes.get("include") is not None
        self.template_name = self.attributes.get("include")
        self.content = TemplatePart(self.body)
        self.template_data = lambda d: tag_string.replace(self.body, "")


SuitTagsMap = {
    "var": Variable, "iterationvar": IterationVariable, "iterationkey": IterationKey,
    "if": Condition, "list": List, "expression": Expression, "breakpoint": Breakpoint
}


class TemplatePart(object):
    """
    Class TemplatePart.
    Represents any kind of textual content with some tags inside or without
    For example,
    "hello<span>, </span>world!" is not valid xml-tag,
    but it's a normal TemplatePart string
    """

    def __init__(self, text, tags_to_process=None):
        text = trimSpaces(text)
        self.text = text
        self.cdata = []
        self.tags = None
        self.tags_pattern = None
        self.tags_counter = TagCounter(tags_to_process)

        if tags_to_process is None:
            tags_to_process = SuitTags
        self.parseTags(tags_to_process)

    def parseTags(self, tags_to_process):
        """
        Defines tags to be parsed from template
        :param: list:   tags_to_process:    List of tag names to be parsed from template
        """
        self.tags = tags_to_process
        self.tags_pattern = re.compile(
            '<(?P<tagName>(?:%s)+(_\d+)?)(?:\s.+?)*>(?:.?)*</(?P=tagName)>' % (
                "|".join(tags_to_process)
            ), re.DOTALL
        )
        self.text = re.sub(
            self.tags_pattern,
            lambda m: "{{ph:%d}}" % (self.cdata.append(m.group(0)) or len(self.cdata) - 1),
            self.tags_counter.count(self.text)
        )

    def getText(self):
        """
        Retruns a string representing template.
        If template part had some tags inside and they were defined by setTags() - result of getText() will be
        template string, where any occurancies of that tags will be replaced by "%s" placeholder
        :return: string
        """
        return self.text

    def getData(self):
        """
        Returns a list of all tags found in Template Part and defined by setTags() method
        :return: list
        """
        return self.cdata

    def getTags(self):
        """
        Returns a list of the XmlTag objects corresponding to getData() method
        :return:
        """
        return [self.toSuitTag(tag_text) for tag_text in self.cdata]

    def getDataForCompile(self):
        """
        Returns a tuple (text, tags) for compiler
        :return: tuple
        """
        return self.getText(), self.getTags()

    def toSuitTag(self, tag_text):
        name = tag_text.split(" ")[0].split(">")[0].replace("<", "").split("_")[0]
        suit_tag = SuitTagsMap.get(name) or XmlTag
        return suit_tag(tag_text)


class Template(object):
    def __init__(self, templateName):
        self.tags_counter = TagCounter()
        self.templateName = templateName

        # На случай если идет обращение по абсолютному пути ищем шаблон поднимаясь по дереву директорий:
        initial_dir = os.path.realpath(os.path.curdir)
        if not os.path.isfile(templateName):
            first, *tale = templateName.split("/")
            attempts = 10
            while os.path.basename(os.path.realpath(os.path.curdir)) and os.path.basename(
                    os.path.realpath(os.path.curdir)) != first and attempts > 0:
                attempts -= 1
                os.chdir("../")
            os.chdir("../")
            if not os.path.isfile(templateName):
                raise TemplateNotFound("template %s not found" % templateName)

        f = open(templateName)
        self.content = "".join(f.readlines())
        f.close()
        os.chdir(initial_dir)

        self.content = re.sub("<!--(.+?)-->", "", self.content)  # cut all comments
        self.css, self.js = None, None
        self.parse_resources("css", "<style(?:\s.+?)*>(.*?)</style>")  # cut & save css
        self.parse_resources("js", "<script>(.*?)</script>")  # cut & save js
        self.rebase()
        self.include()

    def getContent(self):
        return self.content

    def getBreakPoints(self, content, all_levels=False):
        """
        Returns a map of all breakpoints found in template.
        Use all_levels = True for recursion

        :param all_levels:  Do we need to look deeper than one level of nested tags
        :param content:     Content to be parsed
        :return: dict:      map of breakpoints {name: tag}
        """
        content = self.tags_counter.count(content)
        breakpointsMap = {}
        bps = re.findall('(<breakpoint(?P<brcount>[_\d]*)(?:\s.+?)*>.*?</breakpoint(?P=brcount)>)', content, re.DOTALL)
        for bp in bps:
            bp_element = Breakpoint(bp[0])
            if bp_element.get("name"):
                breakpointsMap[bp_element.get("name")] = self.tags_counter.decount(bp[0])
                if all_levels:
                    nextLevel = self.getBreakPoints(bp_element.body, all_levels)
                    for bpname in nextLevel:
                        breakpointsMap[bpname] = nextLevel[bpname]
        return breakpointsMap

    def parse_resources(self, res_type, regexp):
        """ Excludes all css styles from template and stores them in self """
        match = re.search(regexp, self.content, re.DOTALL)
        if match is not None:
            self.content = self.content.replace(match.group(0), "")
            self.__dict__[res_type] = match.group(1)

    def rebase(self):
        """ Performs a rebase operation if template contains <rebase> tag """
        parentTemplateName = re.search('<rebase(?:\s.+?)*>(.+?)</rebase>', self.content, re.DOTALL)
        if parentTemplateName is None:
            return
        parent = Template(parentTemplateName.group(1).strip("'").strip("\"").replace(".", "/") + ".html")
        parent.content = re.sub("\s\s+", " ", parent.content).strip()
        rebased_template = re.sub("\s\s+", " ", parent.content).strip()
        bp_parent = parent.getBreakPoints(parent.content, all_levels=True)
        bp_current = self.getBreakPoints(self.content)
        for bp_name in bp_parent:
            if bp_current.get(bp_name):
                rebased_template = rebased_template.replace(bp_parent[bp_name], bp_current[bp_name])
        self.content = rebased_template

    def include(self):
        """ Includes all sub templates if template contains <breakpoint> tags with 'include' attribute """
        self.content = re.sub(
            '(<breakpoint(?P<brcount>(?:_\d+)?) include=(.+?)></breakpoint(?P=brcount)>)',
            lambda m: Template(m.group(3).strip("'").strip("\"").replace(".", "/") + ".html").getContent(),
            self.content
        )

    def compile(self, languageEnginesMap):
        """
        Compiles itself into source code according given map
        :param languageEnginesMap:
        :return:
        """
        template_part = TemplatePart(self.content)
        compiled = {
            language: languageEnginesMap[language]().compile(
                template_part.getDataForCompile()
            ) for language in languageEnginesMap
        }

        # Compiling python source
        templateName = self.templateName.replace(".html", "").replace("/", "_")
        pythonSource = "from suit.Suit import Suit, SuitRunTime, SuitNone, SuitFilters\n" \
                       "class %s(object):\n" \
                       "\tdef execute(self, data={}):\n" \
                       "\t\tself.data = data\n" \
                       "\t\treturn (%s)" % (templateName, compiled["py"])
        f = open("__py__/%s" % self.templateName.replace("/", "_").replace("html", "py"), "w+")
        f.writelines(pythonSource)
        f.close()

        # Build css
        f = open("__css__/%s" % self.templateName.replace("/", "_").replace("html", "css"), "w+")
        f.writelines("".join(self.css or ""))
        f.close()

        # Build js
        jsCompiled = compiled["js"]
        jsApiInit = self.js.strip() if self.js else "null"
        jsSource = 'suit.SuitApi.addTemplate({template}, {jsCompiled}, {jsApiInit});\n' \
            .format(
            template='"%s"' % self.templateName.replace(".html", "").replace("/", "."),
            jsCompiled='function(data) {data = data || {}; return %s}' % jsCompiled,
            jsApiInit=jsApiInit
        )

        f = open("__js__/%s" % self.templateName.replace("/", "_").replace("html", "js"), "w+")
        f.writelines(jsSource)
        f.close()


class Syntax(metaclass=ABCMeta):
    """ Abstract Class For Creating Language Engines """

    def try_compile(self, text):
        """ Tries to compile given string """
        if text is not None:
            return self.compile(TemplatePart(text).getDataForCompile())

    def compile_tag(self, tag):
        """ Compiles given SuitTag into source code """

        if isinstance(tag, IterationKey):
            return tag.var_name

        elif isinstance(tag, IterationVariable):
            filters = [
                lambda var: self.filter(filter_name, var, self.try_compile(filter_data))
                for filter_name, filter_data in tag.filters
            ]
            return self.var(tag.var_name, filters, self.try_compile(tag.default))

        elif isinstance(tag, Variable):
            filters = [
                lambda var: self.filter(filter_name, var, self.try_compile(filter_data))
                for filter_name, filter_data in tag.filters
            ]
            return self.var(tag.var_name, filters, self.try_compile(tag.default))

        elif isinstance(tag, Condition):
            return self.condition(
                self.compile(tag.condition.getDataForCompile()),
                self.compile(tag.true.getDataForCompile()),
                self.compile(tag.false.getDataForCompile())
            )

        elif isinstance(tag, List):
            return self.list(
                self.compile(tag.iteration_template.getDataForCompile()),
                tag.iterkey,
                self.var(tag.iterable.var_name, without_stringify=True)
            )

        elif isinstance(tag, Expression):
            return self.expression(self.compile(tag.expresion_body.getDataForCompile()))

        elif isinstance(tag, Breakpoint):
            if tag.body and tag.body.startswith("{"):
                return self.include(tag.template_name, tag.body)
            else:
                return self.compile(tag.content.getDataForCompile())

        else:
            raise None

    @abstractmethod
    def compile(self, data):
        pass

    @abstractmethod
    def convertplaceholders(self, template):
        pass

    @abstractmethod
    def var(self, var_name, filters=None, default=None, without_stringify=False):
        pass

    @abstractmethod
    def include(self, bp_name, bp_body):
        pass

    @abstractmethod
    def condition(self, condition, true, false):
        pass

    @abstractmethod
    def list(self, template, itervar, iterable):
        pass

    @abstractmethod
    def expression(self, expression):
        pass

    @abstractmethod
    def filter(self, filterName, var, data=None):
        pass

    def logicand(self):
        return "&&"

    def logicor(self):
        return "||"

    def true(self):
        return "true"

    def false(self):
        return "false"


class PythonSyntax(Syntax):
    """
    Класс, обеспечивающий возможность компиляции шаблонов в исходный код python
    """

    def compile(self, data):
        template, tags = data
        template = template.replace('"', '\\"')
        template = self.convertplaceholders(template)
        template = re.sub("(%[^sdmiHMyS])", lambda m: "%%%s" % m.group(1), template)
        if len(tags) > 0:
            return '"' + template + '" % (' + ", ".join([self.compile_tag(t) for t in tags]) + ')'
        else:
            return ('"' + template + '"').replace("%%", "%")

    def convertplaceholders(self, template):
        return re.sub("\{\{ph:\d+\}\}", "%s", template)

    def include(self, bp_name, bp_body):
        return "SuitRunTime.include({}, '%s', lambda: self.data, '%s')" % (bp_name, bp_body)

    def var(self, var_name, filters=None, default=None, without_stringify=False):
        if filters is None:
            filters = []
        res = "SuitRunTime.var(lambda self: self.data%s, %s, self)" % (var_name, default)
        for filter_lambda in filters:
            res = filter_lambda(res)
        if without_stringify is False:
            return "SuitRunTime.stringify(%s)" % res
        else:
            return res

    def condition(self, condition, true, false):
        condition = condition.replace("&&", self.logicand())
        condition = condition.replace("||", self.logicor())
        condition = condition.replace("true", self.true())
        condition = condition.replace("false", self.false())
        return '''SuitRunTime.opt(%s, lambda: %s, lambda: %s)''' % (condition, true, false if false else "")

    def list(self, template, itervar, iterable):
        inc_data = re.search("SuitRunTime.include\(({.*?}), ", template, re.DOTALL)
        inc_data = inc_data.group(1) if inc_data else "{}"
        new_inc_data = inc_data
        if inc_data:
            iter_addition = '''"%s": %s''' % (itervar, itervar)
            new_inc_data = '{%s}' % iter_addition if len(inc_data) == 2 else inc_data.rstrip(
                "}") + ", " + iter_addition + "}"
        template = template.replace("SuitRunTime.include(%s, " % inc_data, "SuitRunTime.include(%s, " % new_inc_data)
        return '''SuitRunTime.list(lambda %s: %s, %s)''' % (itervar, template, iterable)

    def expression(self, expression):
        return "SuitRunTime.expression(%s)" % expression

    def filter(self, filterName, var, data=None):
        if data is None:
            return '''SuitFilters._%s(%s)''' % (filterName, var)
        else:
            return '''SuitFilters._%s(%s, %s)''' % (filterName, var, data)

    def logicand(self):
        return "and"

    def logicor(self):
        return "or"

    def true(self):
        return "True"

    def false(self):
        return "False"


class JavascriptSyntax(Syntax):
    """
    Класс, обеспечивающий возможность компиляции шаблонов в исходный код javascript

    """

    def compile(self, data):
        template, tags = data
        template = template.replace('"', '\\"')
        template = self.convertplaceholders(template)
        if len(tags) > 0:
            return '"' + template + '".format(' + ", ".join([self.compile_tag(t) for t in tags]) + ')'
        else:
            return '"' + template + '"'

    def convertplaceholders(self, template):
        return re.sub('\{\{ph:(\d+)\}\}', lambda m: "{%s}" % m.group(1), template)

    def include(self, bp_name, bp_body):
        template_part = TemplatePart(bp_body)
        compiled = self.compile(template_part.getDataForCompile())
        compiled = '''function(data) { return %s ; }''' % compiled
        return "suit.SuitRunTime.include({}, '%s', function() { return data }, %s)" % (bp_name, compiled)

    def var(self, var_name, filters=None, default=None, without_stringify=False):
        if filters is None:
            filters = []
        res = "suit.SuitRunTime.variable(function(){ return data%s; }, %s)" % (
            var_name, default if default is not None else "null"
        )
        for filter_lambda in filters:
            res = filter_lambda(res)
        return res if without_stringify else "suit.SuitRunTime.stringify(%s)" % res

    def condition(self, condition, true, false):
        return 'suit.SuitRunTime.opt(%s, function() {return (%s)}, function() {return (%s)})' % (condition, true, false)

    def list(self, template, itervar, iterable):
        inc_data = re.search("suit.SuitRunTime.include\(({.*?}), ", template, re.DOTALL)
        inc_data = inc_data.group(1) if inc_data else "{}"
        new_inc_data = inc_data
        if inc_data:
            iter_addition = '''"%s": %s''' % (itervar, itervar)
            new_inc_data = '{%s}' % iter_addition if len(inc_data) == 2 else inc_data.rstrip(
                "}") + ", " + iter_addition + "}"
        template = template.replace("suit.SuitRunTime.include(%s, " % inc_data,
                                    "suit.SuitRunTime.include(%s, " % new_inc_data)

        return '''suit.SuitRunTime.list(function(%s) { return %s; }, (%s))''' % (
            itervar, template.replace(".%s)" % itervar, "[%s])" % itervar), iterable)

    def expression(self, expression):
        return "eval(%s)" % expression

    def filter(self, filterName, var, data=None):
        if filterName == "length":
            var = '''suit.SuitFilters.get_length(%s, %s)''' % (var, var)
        elif filterName == "startswith":
            var = "suit.SuitFilters.startswith(%s, %s)" % (var, data)
        elif filterName == "in":
            var = "suit.SuitFilters.inArray(%s, %s)" % (var, data)
        elif filterName == "notin":
            var = "!suit.SuitFilters.inArray(%s, %s)" % (var, data)
        elif filterName == "contains":
            var = "suit.SuitFilters.contains(%s, %s)" % (var, data)
        elif filterName == "bool":
            return "suit.SuitFilters.to_bool(%s)" % var
        elif filterName == "int":
            return "suit.SuitFilters.str2int(%s)" % var
        elif filterName == "str":
            return '''suit.SuitFilters.to_str(%s)''' % var
        elif filterName == "dateformat":
            return '''suit.SuitFilters.dateformat(%s, %s)''' % (var, data)
        elif filterName == "usebr":
            return '''suit.SuitFilters.usebr(%s)''' % var
        elif filterName == "plural_form":
            return '''suit.SuitFilters.plural_form(%s, %s)''' % (var, data)
        elif filterName == "html":
            return '''suit.SuitFilters.html(%s)''' % var
        var = "suit.SuitRunTime.stringify(%s)" % var
        return var


class Compiler(object):
    def compile(self, path="."):
        """
        Компилирует все найденные шаблоны внутри указанного каталога

        :param path:    Путь до каталога с шаблонами
        """
        self._checkCompiledPackage()
        for file in os.listdir(path):
            target = (path + "/" + file) if path != "." else file
            if os.path.isdir(target):
                self.compile(target)
            elif os.path.isfile(target):
                if self._isTemplateName(target) is False:
                    continue
                template = Template(target)
                template.compile({"py": PythonSyntax, "js": JavascriptSyntax})

    def build(self):
        """
        Собирает js-шаблоны в билды согласно их размещению в каталогах

        """
        for file in os.listdir("."):
            if os.path.isdir(file):
                self._build_catalog(file, "js")
                self._build_catalog(file, "css")
        self._build_all("js")
        self._build_all("css")

    def _build_all(self, fileType):
        """
        Собирает общую библиотеку всех fileType-файлов

        """
        all_content = []
        for file in os.listdir("__%s__" % fileType):
            if os.path.isfile("__%s__/" % fileType + file) and \
                    file.endswith(".%s" % fileType) and \
                            file.startswith("all.") is False:
                f = open("__%s__/%s" % (fileType, file))
                all_content += f.readlines()
                f.close()

        f = open("__%s__/all.%s" % (fileType, fileType), "w+")
        f.writelines("".join(all_content))
        f.close()

    def _build_catalog(self, path, fileType):
        """
        Собирает шаблоны внутри каталога в единый файл, готовый для подключения к проекту

        :param path:      Каталог, внутри которого необходимо собрать шаблоны
        """
        if path.startswith("__"):
            return

        path = path.strip("/")

        for file in os.listdir(path):
            if os.path.isdir(path + "/" + file):
                self._build_catalog(path + "/" + file, fileType)

        files = os.listdir("__%s__" % fileType)
        content = []
        for file in files:
            if file.startswith(path.replace("/", "_")) & file.endswith(fileType):
                f = open("__%s__/%s" % (fileType, file))
                content += f.readlines()
                f.close()

        f = open("__%s__/all.%s.%s" % (fileType, path.replace("/", "."), fileType), "w+")
        f.writelines("".join(content))
        f.close()

    def _checkCompiledPackage(self):
        """
        Проверяет наличие каталога __py__ и файла __init__.py в нем
        Создает в случае остутствия

        """
        os.chmod("../views/", 0o777)

        if os.path.isfile("__init__.py") is False:
            f = open("__init__.py", "w+")
            f.close()

        if os.path.isdir("__js__") is False:
            os.mkdir("__js__")
            os.chmod("__js__", 0o777)

        if os.path.isdir("__py__") is False:
            os.mkdir("__py__")
            os.mkdir("__py__/__pycache__")
            os.chmod("__py__", 0o777)
            os.chmod("__py__/__pycache__", 0o777)

        if os.path.isdir("__css__") is False:
            os.mkdir("__css__")
            os.chmod("__css__", 0o777)

        if os.path.isdir("__pycache__") is False:
            os.mkdir("__pycache__")
            os.chmod("__pycache__", 0o777)

        if os.path.isfile("__py__/__init__.py") is False:
            f = open("__py__/__init__.py", "w+")
            f.close()

    def _isTemplateName(self, path):
        """
        Проверяет корректность параметра path, если подразумевается, что path - это путь до исходника шаблона

        :param path:    Путь до шаблона
        :raise:         InvalidCompilerOptionsException
        """
        if path.endswith(".html") is False or os.path.isfile(path) is False:
            return False


# ########################################## RunTime Classes ##########################################################


class Suit(object):
    """
    Suit execution wrapper
    """

    def __init__(self, path):
        if not path.startswith("{"):
            path = path.split(".")
            self.template = None
            for i in range(len(path)):
                cpath = "%s/__py__/" % "/".join(path[:len(path) - i])
                if os.path.isdir(cpath):
                    template_name_part = "_".join(path[len(path) - i:])
                    module = importlib.import_module("%s%s" % (cpath.replace("/", "."), template_name_part))
                    template_class = getattr(module, template_name_part)
                    self.template = template_class()
            if not self.template:
                raise TemplateNotFound("template not found")
        else:
            template_part = TemplatePart(path)
            compiled = PythonSyntax().compile(template_part.getDataForCompile())
            self.template = "lambda self: %s" % compiled
            self.template = re.sub('\[itervar_(.+?)\]', lambda m: '[self.data["itervar_%s"]]' % m.group(1),
                                   self.template)

    def execute(self, data=None):
        """
        Executes a template
        :param data: data for template execution
        :return:     result of template execution
        """
        if data is None:
            data = {}
        if hasattr(self.template, "execute"):
            res = self.template.execute(data)
            # поддержка internal.data, suit.environment и auto-refresh на стороне клиента:
            if res.startswith("<!DOCTYPE html>") and res.find("auto-refresh") > -1:
                exclude = data.get("suit_environment_exclude")
                if exclude:
                    suit_env_data = {key: val for key, val in data.items() if key not in exclude}
                else:
                    suit_env_data = data
                res = res.replace("</head>",
                                  '''<script id="suit_environment_script">window.suit_environment='%s'</script></head>''' % json_safedumps(
                                      suit_env_data))
            return res
        else:
            # noinspection PyAttributeOutsideInit
            self.data = data
            return eval(self.template)(self)


def suit(templateName):
    """ Suit decorator """

    def decorator(func):
        def wrapped(*args, **kwargs):
            data = func(*args, **kwargs)
            if isinstance(data, str) and len(data) > 0:
                return data
            elif isinstance(data, dict) is True:
                return Suit(templateName).execute(data)
            else:
                try:
                    return Suit(templateName).execute()
                except KeyError:
                    return data
                except NameError:
                    return data

        return wrapped

    return decorator


class SuitRunTime(object):
    """ RunTime helpers """

    @staticmethod
    def stringify(obj):
        """ Prints variable """
        return json.dumps(obj, default=json_dumps_handler) if isinstance(obj, (list, dict)) else obj

    @staticmethod
    def var(lambdavar, default, context):
        """
        Calls variable in safe way, avoids exceptions and return SuitNone() in case of missing required variable
        :param lambdavar:  lambda function, which should return a variable's value
        :param default:    default value
        :param context:    execution context (object that contains template's data in it's attributes)
        """

        def safedefault():
            """ Returns default value or SuitNone() """
            return default if default is not None else SuitNone()

        try:
            res = lambdavar(context)
            if res is None:
                return safedefault()
            return escape(res, quote=True) if isinstance(res, str) else res
        except NameError:
            return safedefault()
        except KeyError:
            return safedefault()
        except IndexError:
            return safedefault()
        except TypeError:
            return safedefault()

    @staticmethod
    def opt(condition, true, false):
        """
        Returns the result depending on evaluating of the condition
        :param condition:   string condition
        :param true:        lambda function used if condition is true
        :param false:       lambda function used if condition is false
        :return:
        """
        return true() if eval(condition) else false()

    @staticmethod
    def list(iterationGenerator, iterable):
        """
        Returns the result of an iteration
        :param iterationGenerator:  lambda function that generates template on each cycle iteration
        :param iterable:            iterable object
        :return: str:               result of cycle
        """
        iterable = range(0, len(iterable)) if isinstance(iterable, list) else iterable
        return "".join([iterationGenerator(itervar) for itervar in iterable])

    @staticmethod
    def expression(expression):
        """
        Evaluates an expression
        :param expression:          expression string
        :return:                    result of evaluation
        """
        return eval(expression)

    @staticmethod
    def include(iter_dict, template_name, main_data, datatemplate_part_to_become_data):
        from copy import deepcopy
        from collections import OrderedDict

        main_data = main_data()
        new_data = deepcopy(main_data)
        for key in iter_dict:
            new_data["itervar_%s" % key] = iter_dict[key]
            datatemplate_part_to_become_data = datatemplate_part_to_become_data.replace('[%s]' % key,
                                                                                        '[itervar_%s]' % key)
        try:
            scope_data = json.loads(Suit(datatemplate_part_to_become_data).execute(new_data),
                                    object_pairs_hook=OrderedDict)
            new_data.update(scope_data)
        except ValueError:
            print("!!! ERROR !!! INVALID JSON: %s" % Suit(datatemplate_part_to_become_data).execute(new_data))
        return Suit("views.%s" % template_name).execute(new_data)


class SuitFilters(object):
    """
    Базовый класс, предоставляющий функционал фильтров (декораторов) для применения к переменным

    """

    @staticmethod
    def _length(var):
        return len(str(var) if isinstance(var, (int, float)) is True else var) if var not in [None, ""] else 0

    @staticmethod
    def _startswith(var, data=None):
        return var.startswith(data) if isinstance(data, SuitNone) is False else False

    @staticmethod
    def _in(var, data):
        if not data:
            return False
        if data and isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                data = data or []
        if not isinstance(data, (dict, list, tuple)):
            return False
        return (var in data) if (isinstance(var, SuitNone) is False and isinstance(data, SuitNone) is False) else False

    @staticmethod
    def _notin(var, data):
        return SuitFilters._in(var, data) is False

    @staticmethod
    def _contains(haystack, needle):
        return SuitFilters._in(needle, haystack)

    @staticmethod
    def _bool(var):
        if str(var).lower() in ["false", "none", "", "0"] or isinstance(var, SuitNone):
            return False
        else:
            return bool(var)

    @staticmethod
    def _int(var):
        return int(var) if isinstance(var, SuitNone) is False else 0

    @staticmethod
    def _dateformat(var, format_str):
        if isinstance(var, (datetime, date)):
            return var.strftime(format_str)
        elif isinstance(var, str):
            try:
                return datetime.strptime(var, '%a %b %d %H:%M:%S %Y').strftime(format_str)
            except ValueError:
                return var
        return var

    @staticmethod
    def _str(var):
        return '''"%s"''' % var

    @staticmethod
    def _usebr(var):
        return re.sub("\n", "<br />", var, re.MULTILINE)

    @staticmethod
    def _html(var):
        return unescape(var)

    @staticmethod
    def _plural_form(initial_num, words):
        initial_num = initial_num if initial_num else 0
        if words:
            words = json.loads(words)
        num = int(initial_num) % 100
        if num > 19:
            num %= 10
        if num == 1:
            word = words[0]
        elif num == 2 or num == 3 or num == 4:
            word = words[1]
        else:
            word = words[2]
        return "%d %s" % (initial_num, word)


class SuitNone(object):
    """ Represents None, but with more complicated logic """

    def __init__(self, value=None):
        self.value = value

    def get(self, key):
        return self.value

    def __str__(self):
        return self.value if self.value is not None else "SuitNone()"

    def __getitem__(self, key):
        return SuitNone(self.value)

    def __len__(self):
        return 0

    def __gt__(self, other):
        return other < 0

    def __ge__(self, other):
        return other <= 0

    def __lt__(self, other):
        return other > 0

    def __le__(self, other):
        return other >= 0

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    def __iter__(self):
        for it in []:
            yield it

    def startswith(self, prefix):
        return False

    def strftime(self, format_str):
        return ""


def json_dumps_handler(obj):
    """ json dumps handler """
    if isinstance(obj, time):
        obj = datetime(1970, 1, 1, obj.hour, obj.minute, obj.second)
        return obj.ctime()
    if isinstance(obj, datetime) or isinstance(obj, date):
        return obj.ctime()
    return None


def json_loads_handler(data):
    """ json loads handler """
    from datetime import datetime

    for k, v in data.items():
        if isinstance(v, str) and re.search("\w\w\w[\s]+\w\w\w[\s]+\d[\d]*[\s]+\d\d:\d\d:\d\d[\s]+\d\d\d\d", v):
            try:
                data[k] = datetime.strptime(v, "%a %b %d %H:%M:%S %Y")
            except Exception as err:
                raise err
    return data


def json_safedumps(content):
    """
    This function "safely" dumps a JSON string that could be injected into a front-end template inside a JavaScript quoted literal, i.e.
    <script>
    var data = JSON.parse("{{ encoded_data }}");
    </script>
    So, quotes in the JSON needed to be escaped to not conflict with the string delimiters, newlines had to be removed or they'd cause a JavaScript syntax error, and so-on.
    - - -
    Originally, the two __literal_slash__ lines didn't exist, and it would fall apart if some of the text had a literal "\n" sequence written out in text, i.e. "\\n", as in this example:
    >>> data = {"message": "Hello\\nworld!"}
    >>> json.dumps(data)
    {"message": "Hello\\nworld!"}
    What would happen was that the "\\n" substitution would end up matching the "\n" from "\\n" and removing it, leaving an orphaned, single "\" character behind. If that character then ended up touching another letter and it didn't form a valid JSON escape sequence (for example, "\a"), this would cause a JSON parse error in the JavaScript.
    So, they first rename literal \ characters to __literal_slash__, do all the other substitutions, and then rename it back.
    """
    return json.dumps(content, default=json_dumps_handler) \
        .replace('\\\\', '__literal_slash__') \
        .replace('\\n', '') \
        .replace('\\r', '') \
        .replace('\\"', '\\\\"') \
        .replace("'", "\\'") \
        .replace('__literal_slash__', '\\\\\\\\')


def trimSpaces(string):
    """ Trims multiple spaces from string, leaves just one space instead """
    string = string.replace("\t", "  ").replace("\n", "  ").replace("\r", "  ")
    string = re.sub(">\s\s+", ">", string)
    string = re.sub("\s\s+<", "<", string)
    string = re.sub("\s\s+", " ", string, flags=re.MULTILINE)
    string = string.strip(" ")
    return string
