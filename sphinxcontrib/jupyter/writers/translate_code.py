import docutils.nodes
import re
import nbformat.v4
import os.path
import datetime
from .utils import LanguageTranslator, JupyterOutputCellGenerators, get_source_file_name


class JupyterCodeTranslator(docutils.nodes.GenericNodeVisitor):
    URI_SPACE_REPLACE_FROM = re.compile(r"\s")
    URI_SPACE_REPLACE_TO = "-"

    def __init__(self, builder, document):
        docutils.nodes.NodeVisitor.__init__(self, document)

        self.lang = None
        self.nodelang = None

        self.langTranslator = LanguageTranslator(builder.config["templates_path"])

        # Reporter
        self.warn = self.document.reporter.warning
        self.error = self.document.reporter.error

        # Settings
        self.settings = document.settings
        self.builder = builder
        self.source_file_name = get_source_file_name(
            self.settings._source,
            self.settings.env.srcdir)
        self.default_lang = "python3"

        # Create output notebook
        self.output = nbformat.v4.new_notebook()

        # Variables defined in conf.py
        self.jupyter_kernels = builder.config["jupyter_kernels"]
        self.jupyter_headers = builder.config["jupyter_headers"]
        self.jupyter_write_metadata = builder.config["jupyter_write_metadata"]

        # Override conf.py with any command line options that have been sent through.
        using_autosave = builder.config["jupyter_python_autosave"]
        if not using_autosave:
            self.jupyter_headers['python3'] = []

        # Welcome message block
        template_paths = builder.config["templates_path"]
        welcome_block_filename = builder.config["jupyter_welcome_block"]

        full_path_to_welcome = None
        for template_path in template_paths:
            if os.path.isfile(template_path + "/" + welcome_block_filename):
                full_path_to_welcome = os.path.normpath(template_path + "/" + welcome_block_filename)

        if full_path_to_welcome:
            with open(full_path_to_welcome) as input_file:
                lines = input_file.readlines()

            line_text = "".join(lines)
            formatted_line_text = self.strip_blank_lines_in_end_of_block(line_text)
            nb_header_block = nbformat.v4.new_markdown_cell(formatted_line_text)

            # Add the welcome block to the output stream straight away
            self.output["cells"].append(nb_header_block)

        # Write metadata
        if self.jupyter_write_metadata:
            meta_text = \
                "Notebook created: {:%Y-%m-%d %H:%M:%S}  \n" \
                "Generated from: {}  "

            metadata = meta_text.format(
                datetime.datetime.now(),
                self.source_file_name)

            self.output["cells"].append(nbformat.v4.new_markdown_cell(metadata))

        # Variables used in visit/depart
        self.in_code_block = False  # if False, it means in markdown_cell
        self.output_cell_type = None
        self.code_lines = []

    # generic visit and depart methods
    # --------------------------------
    simple_nodes = (
        docutils.nodes.TextElement,
        docutils.nodes.image,
        docutils.nodes.colspec,
        docutils.nodes.transition)  # empty elements

    def default_visit(self, node):
        pass

    def default_departure(self, node):
        pass

    # specific visit and depart methods
    # ---------------------------------

    # ==============
    #  Sections
    # ==============
    def visit_document(self, node):
        """
        at start
        """
        # we need to give the translator a default language!
        # the translator needs to know what language the document is written in
        # before depart_document is called.
        self.lang = self.default_lang

    def depart_document(self, node):
        """
        at end
        """
        if not self.lang:
            self.warn(
                "Highlighting language is not given in .rst file. "
                "Set kernel as default(python3)")
            self.lang = self.default_lang

        # Header(insert after metadata)
        if self.jupyter_headers is not None:
            if self.lang in self.jupyter_headers:
                for h in self.jupyter_headers[self.lang][::-1]:
                    if self.jupyter_write_metadata:
                        self.output["cells"].insert(1, h)
                    else:
                        self.output["cells"].insert(0, h)
            else:
                self.warn(
                    "Invalid jupyter headers. "
                    "jupyter_headers: {}, lang: {}"
                        .format(self.jupyter_headers, self.lang))

        # Update metadata
        if self.jupyter_kernels is not None:
            if "kernelspec" in self.jupyter_kernels[self.lang]:
                self.output.metadata.kernelspec = self.jupyter_kernels[self.lang]["kernelspec"]
            else:
                self.warn(
                    "Invalid jupyter kernels. "
                    "jupyter_kernels: {}, lang: {}"
                        .format(self.jupyter_kernels, self.lang))

    def visit_highlightlang(self, node):
        lang = node.attributes["lang"].strip()
        if lang in self.jupyter_kernels:
            self.lang = lang
        else:
            self.warn(
                "Highlighting language({}) is not defined "
                "in jupyter_kernels in conf.py. "
                "Set kernel as default(python3)"
                    .format(lang))
            self.lang = self.default_lang

    # =================
    # Inline elements
    # =================
    def visit_Text(self, node):
        text = node.astext()
        if self.in_code_block:
            self.code_lines.append(text)

    def depart_Text(self, node):
        pass

    # ================
    #  code blocks
    # ================
    def visit_literal_block(self, node):
        self.output_cell_type = JupyterOutputCellGenerators.GetGeneratorFromClasses(node.attributes['classes'])
        self.nodelang = node.attributes["language"].strip() if "language" in node.attributes else self.lang

        # Translate the language name across from the Sphinx to the Jupyter namespace
        self.nodelang = self.langTranslator.translate(self.nodelang)

        self.in_code_block = True
        self.code_lines = []

        # If the cell being processed contains code written in a language other than the one that
        # was specified as the default language, do not create a code block for it - turn it into
        # markup instead.
        if self.nodelang != self.langTranslator.translate(self.lang):
            self.output_cell_type = JupyterOutputCellGenerators.MARKDOWN

    def depart_literal_block(self, node):
        line_text = "".join(self.code_lines)
        formatted_line_text = self.strip_blank_lines_in_end_of_block(line_text)

        new_code_cell = self.output_cell_type.Generate(formatted_line_text, self)
        if self.output_cell_type is JupyterOutputCellGenerators.CODE_OUTPUT:
            # Output blocks must  be added to code cells to make any sense.
            # This script assumes that any output blocks will immediately follow a code
            # cell; a warning is raised if the cell immediately preceding this output
            # block is not a code cell.
            #
            # It is assumed that code cells may only have one output block - any more than
            # one will raise a warning and be ignored.
            most_recent_cell = self.output["cells"][-1]
            if most_recent_cell.cell_type != "code":
                self.warn(
                    "Warning: Class: output block found after a " + most_recent_cell.cell_type + " cell. Outputs may only come after code cells.")
            elif most_recent_cell.outputs:
                self.warn(
                    "Warning: Multiple class: output blocks found after a code cell. Each code cell may only be followed by either zero or one output blocks.")
            else:
                most_recent_cell.outputs.append(new_code_cell)
        else:
            self.output["cells"].append(new_code_cell)

        self.in_code_block = False

    # ===================
    #  general methods
    # ===================
    @staticmethod
    def strip_blank_lines_in_end_of_block(line_text):
        lines = line_text.split("\n")

        for line in range(len(lines)):
            if len(lines[-1].strip()) == 0:
                lines = lines[:-1]
            else:
                break

        return "\n".join(lines)

