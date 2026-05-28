module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n31,
    n32,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test68/test68.v:10$4_Y , \$auto$rtlil.cc:3255:Not$38 , \$or$testcase/test68/test68.v:11$6_Y , \$xor$testcase/test68/test68.v:12$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test68/test68.v:14$10  (n107, n2, n3);
  and   \$and$testcase/test68/test68.v:17$13  (n109, n2, n3);
  and   \$and$testcase/test68/test68.v:18$14  (n110, n2, n3);
  and   \$and$testcase/test68/test68.v:28$19  (n120, n2, n3);
  and   \$and$testcase/test68/test68.v:34$25  (n126, n2, n3);
  and   llm_named_gate (n100, n2, n3);
  and   \$and$testcase/test68/test68.v:29$20  (n121, n2, n4);
  and   \$and$testcase/test68/test68.v:35$26  (n127, n2, n4);
  not   \$not$testcase/test68/test68.v:19$33  (n111, n4);
  not   \$not$testcase/test68/test68.v:22$35  (n114, n4);
  and   \$and$testcase/test68/test68.v:30$21  (n122, n2, n5);
  not   \$auto$opt_expr.cc:605:replace_const_cells$37  (\$auto$rtlil.cc:3255:Not$38 , n5);
  and   \$and$testcase/test68/test68.v:31$22  (n123, n2, n6);
  and   \$and$testcase/test68/test68.v:32$23  (n124, n2, n7);
  and   \$and$testcase/test68/test68.v:38$29  (n140, n6, n7);
  and   \$and$testcase/test68/test68.v:33$24  (n125, n2, n8);
  or    \$or$testcase/test68/test68.v:15$11  (n108, n107, n5);
  or    \$or$testcase/test68/test68.v:7$2  (n101, n100, n4);
  or    \$or$testcase/test68/test68.v:36$27  (n128, n120, n121);
  or    \$or$testcase/test68/test68.v:24$15  (n22, n114, n100);
  xor   \$xor$testcase/test68/test68.v:37$28  (n129, n122, n123);
  or    \$or$testcase/test68/test68.v:39$30  (n141, n140, n8);
  xor   \$xor$testcase/test68/test68.v:16$12  (n21, n108, n6);
  not   \$not$testcase/test68/test68.v:8$32  (n102, n101);
  and   \$and$testcase/test68/test68.v:42$31  (n130, n129, n128);
  not   \$not$testcase/test68/test68.v:40$36  (n142, n141);
  xor   \$xor$testcase/test68/test68.v:9$3  (n103, n102, n5);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  and   \$and$testcase/test68/test68.v:10$4  (\$and$testcase/test68/test68.v:10$4_Y , n103, n6);
  not   \$not$testcase/test68/test68.v:10$5  (n104, \$and$testcase/test68/test68.v:10$4_Y );
  or    \$or$testcase/test68/test68.v:11$6  (\$or$testcase/test68/test68.v:11$6_Y , n104, n7);
  not   \$not$testcase/test68/test68.v:11$7  (n105, \$or$testcase/test68/test68.v:11$6_Y );
  xor   \$xor$testcase/test68/test68.v:12$8  (\$xor$testcase/test68/test68.v:12$8_Y , n105, n8);
  not   \$not$testcase/test68/test68.v:12$9  (n20, \$xor$testcase/test68/test68.v:12$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = n5;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
